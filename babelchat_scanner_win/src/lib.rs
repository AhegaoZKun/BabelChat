/// babelchat_scanner_win — Windows memory scanner for BabelChat.
///
/// Uses ReadProcessMemory + VirtualQueryEx to scan WoW process memory.
/// Same interface as the Linux scanner: one C export, address cache for
/// near-zero CPU in steady state.
///
/// Fast path: single ReadProcessMemory at cached address (~microseconds).
/// Slow path: parallel region scan on cache miss.
use rayon::prelude::*;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

#[cfg(windows)]
use windows::Win32::{
    Foundation::{CloseHandle, HANDLE},
    System::{
        Diagnostics::Debug::ReadProcessMemory,
        Memory::{VirtualQueryEx, MEMORY_BASIC_INFORMATION, MEM_COMMIT,
                 PAGE_EXECUTE_READ, PAGE_EXECUTE_READWRITE, PAGE_READONLY,
                 PAGE_READWRITE, PAGE_WRITECOPY},
        Threading::{GetCurrentThread, OpenProcess, SetThreadPriority, THREAD_PRIORITY_IDLE,
                    PROCESS_VM_READ, PROCESS_QUERY_INFORMATION},
    },
};

const MARKER: &[u8] = b"__WCT_BUF_";
const MARKER_LEGACY: &[u8] = b"__WCT_BUF__";
const MARKER_END: &[u8] = b"__WCT_END__";
const CHUNK_SIZE: usize = 65536;
const MAX_BUF_READ: usize = 65536;
const MAX_REGION_SIZE: usize = 100 * 1024 * 1024; // 100MB
const MAX_ADDRESS: usize = 0x7FFF_FFFF_FFFF;

// ── Global state ──────────────────────────────────────────────────────────────

struct Cache {
    pid: u32,
    addr: usize,
    /// Cached process handle (raw value). Reused across polls instead of
    /// OpenProcess/CloseHandle 4×/sec; invalidated when a read fails.
    handle: isize,
    /// The pulse this address last showed. A copy whose pulse never advances is
    /// not a quiet chat, it is a corpse.
    last_flush: i64,
    /// When this address last yielded a message, in ms. A cached address that
    /// has gone quiet is not necessarily idle chat: when Lua's GC relocates the
    /// buffer, the bytes it left behind still parse — same markers, same last
    /// sequence — so the fast path reads a ghost forever and never rescans.
    /// That is a silent, permanent stall, and it is what "it works for a minute
    /// and then stops" turned out to be.
    last_fresh_ms: u64,
}

static CACHE: Mutex<Option<Cache>> = Mutex::new(None);
static SCAN_IN_PROGRESS: AtomicBool = AtomicBool::new(false);
/// Rate-limit full scans: if the cache is lost while chat is idle (e.g. after
/// a /reload), scans would otherwise repeat every poll. Minimum gap in ms.
static LAST_SCAN_MS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
const SCAN_MIN_GAP_MS: u64 = 3000;
/// How long a cached address may produce nothing before it is checked by a
/// scan. Long enough that an ordinary lull in chat costs nothing, short enough
/// that a relocated buffer is found while the player is still looking at the
/// message they expected to see translated.
/// How long a cached address may show no pulse before it is checked by a scan.
///
/// Half a second, because with the region tier back a check costs about a
/// millisecond — measured — and every millisecond of patience here is a
/// millisecond a message sits unread. The margin exists only so a momentary
/// bad read does not throw away a good address.
///
/// This was six seconds when a check meant sweeping the heap. That was the
/// price of the missing tier, paid in latency by every message.
const CACHE_MAX_QUIET_MS: u64 = 3_000;
/// The number the addon parks in its saved table and never changes.
///
/// Everything else about the buffer moves. It is a Lua string, so every rebuild
/// allocates a new one somewhere else — fourteen rebuilds landed in fourteen
/// regions twenty gigabytes apart — and searching for something that moves while
/// you search is why every version of this scanner has either burned a core or
/// gone deaf. Measured: the previous release, 48% of one core and five messages
/// a minute.
///
/// A constant can be searched for at leisure. And a Lua table's storage does not
/// move while the table does not rehash, which the addon prevents by declaring
/// all its keys at load. So the slot holding the buffer's pointer sits a few
/// dozen bytes from this number, and reading it gives the current string — no
/// search, at any point, ever again.
const ANCHOR_VALUE: f64 = 8675309123457.0;

/// How long the pulse may stand still at the slot before the table is presumed
/// dead. Three heartbeats: the addon rebuilds at least every two seconds, so
/// six is comfortably beyond doubt and still eight times faster than noticing
/// by the pointer breaking.
const ANCHOR_MAX_QUIET_MS: u64 = 6_000;

/// How far either side of the anchor to look for that slot. The proof found it
/// 112 bytes below.
const ANCHOR_WINDOW: usize = 8192;

struct Anchor {
    pid: u32,
    /// The pulse last read through this slot, and when. A reload leaves the old
    /// table in memory with its buffer frozen at the last thing said before it,
    /// and that reads exactly like a working slot in a quiet chat — the same
    /// confusion the buffer itself caused, one level up.
    last_pulse: i64,
    last_pulse_ms: u64,
    /// Address of the eight bytes holding the pointer to the buffer string.
    slot: usize,
    /// Distance from that pointer to the text — the Lua string header.
    skip: usize,
    handle: isize,
}

static ANCHOR: Mutex<Option<Anchor>> = Mutex::new(None);

/// The highest pulse ever seen in a live buffer.
///
/// A copy left behind by an earlier rebuild cannot have a pulse above this — it
/// stopped being written before the number got here. So a candidate that beats
/// it is the live buffer and the scan can stop, which is what makes an early
/// exit correct. The old early exit compared message counters instead, and a
/// corpse holding the last message ever written matches that just as well.
static LAST_PULSE: std::sync::atomic::AtomicI64 = std::sync::atomic::AtomicI64::new(0);

/// How many full scans this process has run. Diagnostic only.
static SCAN_COUNT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static POOL: std::sync::OnceLock<rayon::ThreadPool> = std::sync::OnceLock::new();

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn get_pool() -> &'static rayon::ThreadPool {
    POOL.get_or_init(|| {
        rayon::ThreadPoolBuilder::new()
            .num_threads(4)
            .thread_name(|i| format!("babelchat-scan-{}", i))
            .start_handler(|_| {
                // The Linux scanner has run its threads at SCHED_IDLE since the
                // day it was written; the Windows one never did, and nobody
                // noticed while the scan was rare. It is not rare: the buffer is
                // a Lua string, so it is reallocated somewhere new on every
                // rebuild, and finding it again is a sweep.
                //
                // THREAD_PRIORITY_IDLE is the same bargain: these threads run
                // only when the game does not want the processor, so the cost
                // lands in the gaps between frames instead of in them.
                #[cfg(windows)]
                unsafe {
                    let _ = SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_IDLE);
                }
            })
            .build()
            .expect("failed to build rayon pool")
    })
}

// ── Process handle ────────────────────────────────────────────────────────────

#[cfg(windows)]
fn open_process(pid: u32) -> Option<HANDLE> {
    unsafe {
        let h = OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION,
            false,
            pid,
        ).ok()?;
        if h.is_invalid() { return None; }
        Some(h)
    }
}

#[cfg(not(windows))]
fn open_process(_pid: u32) -> Option<()> { None }

// ── Memory reading ────────────────────────────────────────────────────────────

#[cfg(windows)]
fn read_memory(handle: HANDLE, addr: usize, buf: &mut [u8]) -> bool {
    let mut bytes_read = 0usize;
    unsafe {
        ReadProcessMemory(
            handle,
            addr as *const _,
            buf.as_mut_ptr() as *mut _,
            buf.len(),
            Some(&mut bytes_read),
        ).is_ok() && bytes_read > 0
    }
}

#[cfg(not(windows))]
fn read_memory(_handle: (), _addr: usize, _buf: &mut [u8]) -> bool { false }

// ── Region enumeration ────────────────────────────────────────────────────────

#[derive(Clone)]
struct Region {
    base: usize,
    size: usize,
}

#[cfg(windows)]
fn get_readable_regions(handle: HANDLE) -> Vec<Region> {
    let readable_protect = [
        PAGE_READWRITE.0, PAGE_READONLY.0,
        PAGE_EXECUTE_READ.0, PAGE_EXECUTE_READWRITE.0,
        PAGE_WRITECOPY.0,
    ];

    let mut regions = Vec::with_capacity(512);
    let mut address = 0usize;

    loop {
        if address >= MAX_ADDRESS { break; }
        let mut mbi = MEMORY_BASIC_INFORMATION::default();
        let result = unsafe {
            VirtualQueryEx(
                handle,
                Some(address as *const _),
                &mut mbi,
                std::mem::size_of::<MEMORY_BASIC_INFORMATION>(),
            )
        };
        if result == 0 { break; }

        let size = mbi.RegionSize;
        if size == 0 { address += 0x1000; continue; }

        if mbi.State == MEM_COMMIT
            && readable_protect.contains(&mbi.Protect.0)
            && size > 0
            && size <= MAX_REGION_SIZE
        {
            regions.push(Region { base: address, size });
        }

        address = address.saturating_add(size);
    }

    // Smallest-first — active Lua strings in smaller regions
    regions.sort_unstable_by_key(|r| r.size);
    regions
}

#[cfg(not(windows))]
fn get_readable_regions(_handle: ()) -> Vec<Region> { vec![] }

// ── Marker helpers ────────────────────────────────────────────────────────────

fn find_content_start(raw: &[u8]) -> Option<usize> {
    if raw.starts_with(b"__WCT_BUF_") {
        if let Some(p) = raw[10..].windows(2).position(|w| w == b"__") {
            return Some(10 + p + 2);
        }
    }
    if raw.starts_with(MARKER_LEGACY) { return Some(MARKER_LEGACY.len()); }
    None
}

fn extract_max_seq(content: &[u8]) -> i32 {
    let mut max_seq = 0i32;
    for line in content.split(|&b| b == b'\n') {
        let line = line.trim_ascii();
        if line.is_empty() { continue; }
        if let Some(pipe) = line.iter().position(|&b| b == b'|') {
            if pipe == 0 { continue; }
            if let Ok(s) = std::str::from_utf8(&line[..pipe]) {
                if let Ok(seq) = s.trim().parse::<i32>() {
                    if seq > max_seq { max_seq = seq; }
                }
            }
        }
    }
    max_seq
}

/// The buffer's pulse: the addon's rebuild counter, written as the first
/// record of every flush and incremented even when nothing was said.
///
/// This is what tells a live buffer from the bytes a previous one left behind.
/// Before it existed, the only thing that moved in the buffer was the message
/// counter, so a copy nobody would ever write to again looked exactly like a
/// quiet chat — and the reader sat on one for minutes at a time.
///
/// Returns 0 for a buffer from an addon that predates the pulse, which ranks it
/// below anything that has one and leaves the old sequence comparison to decide.
fn extract_flush(content: &[u8]) -> i64 {
    const NEEDLE: &[u8] = b"0|META|FLUSH|";
    let start = content
        .windows(NEEDLE.len())
        .position(|w| w == NEEDLE)
        .map(|p| p + NEEDLE.len());
    let Some(start) = start else { return 0 };
    let end = content[start..]
        .iter()
        .position(|&b| b == b'\n' || b == b'\r')
        .map(|p| start + p)
        .unwrap_or(content.len());
    std::str::from_utf8(&content[start..end])
        .ok()
        .and_then(|s| s.trim().parse::<i64>().ok())
        .unwrap_or(0)
}

/// How good a candidate is, most significant first: pulse, then last message.
fn rank(content: &[u8]) -> (i64, i32) {
    (extract_flush(content), extract_max_seq(content))
}

// ── Per-region scan ───────────────────────────────────────────────────────────

#[cfg(windows)]
fn scan_region(handle: HANDLE, region: &Region, _min_seq: i32) -> Option<(usize, Vec<u8>)> {
    let mut offset = 0usize;
    let mut chunk_buf = vec![0u8; CHUNK_SIZE];
    // The best copy in this region rather than the first one found. Returning
    // the first is what let a dead copy win purely by living at a lower address.
    let mut best: Option<((i64, i32), usize, Vec<u8>)> = None;

    while offset < region.size {
        let chunk_size = CHUNK_SIZE.min(region.size - offset);
        let chunk_addr = region.base + offset;
        offset += chunk_size;

        let buf = &mut chunk_buf[..chunk_size];
        if !read_memory(handle, chunk_addr, buf) { continue; }
        if !buf.windows(MARKER.len()).any(|w| w == MARKER) { continue; }

        let mut search = 0;
        while search < buf.len() {
            match buf[search..].windows(MARKER.len()).position(|w| w == MARKER) {
                None => break,
                Some(rel) => {
                    let marker_addr = chunk_addr + search + rel;
                    search += rel + MARKER.len();

                    let mut raw = vec![0u8; MAX_BUF_READ];
                    if !read_memory(handle, marker_addr, &mut raw) { continue; }

                    let cs = match find_content_start(&raw) { Some(s) => s, None => continue };
                    let ep = match raw[cs..].windows(MARKER_END.len()).position(|w| w == MARKER_END) {
                        Some(p) => cs + p,
                        None => continue,
                    };
                    let content = &raw[cs..ep];
                    let score = rank(content);
                    if best.as_ref().is_none_or(|(b, _, _)| score > *b) {
                        best = Some((score, marker_addr, content.to_vec()));
                    }
                }
            }
        }
    }
    best.map(|(_, addr, content)| (addr, content))
}

// ── Full parallel scan ────────────────────────────────────────────────────────

#[cfg(windows)]
fn full_scan(pid: u32, min_seq: i32) -> Option<(usize, Vec<u8>)> {
    let handle = open_process(pid)?;
    let regions = get_readable_regions(handle);

    // No early exit any more. Stopping at the first region that matched is the
    // other half of how a dead copy won: the live buffer is a different, newer
    // allocation, and which of the two a parallel scan reaches first is luck.
    // Reading every region costs one pass; being wrong costs minutes of silence.
    let found = AtomicBool::new(false);
    let result: Mutex<Option<((i64, i32), usize, Vec<u8>)>> = Mutex::new(None);

    // We need one handle per thread — duplicate it for each worker
    get_pool().install(|| {
        // One handle per worker split (for_each_init) instead of one
        // OpenProcess/CloseHandle per region — regions number in the
        // thousands; handles are expensive kernel objects.
        regions.par_iter().for_each_init(
            || open_process(pid).map(|h| h.0 as isize),
            |h, region| {
                let Some(raw) = h else { return };
                let handle = HANDLE(*raw as *mut _);
                if found.load(Ordering::Relaxed) { return; }
                if let Some((addr, content)) = scan_region(handle, region, min_seq) {
                    let score = rank(&content);
                    let mut best = result.lock().unwrap();
                    if best.as_ref().is_none_or(|(b, _, _)| score > *b) {
                        *best = Some((score, addr, content));
                    }
                    // Beating the highest pulse ever seen proves this is the
                    // live buffer, so there is nothing better left to find.
                    //
                    // Only once there IS a highest pulse. On the first scan of
                    // a run the baseline is zero, every candidate beats it, and
                    // stopping at the first one means stopping at whichever the
                    // threads reached first — which was a corpse, measured: a
                    // pulse frozen at 271 for two and a half minutes while the
                    // live buffer went on without us. With no baseline the only
                    // safe thing is to read everything and take the best.
                    let baseline = LAST_PULSE.load(Ordering::Relaxed);
                    if baseline > 0 && score.0 > baseline {
                        found.store(true, Ordering::Relaxed);
                    }
                }
            },
        );
    });

    unsafe { let _ = CloseHandle(handle); }
    result.into_inner().ok()?.map(|(_, addr, content)| (addr, content))
}

#[cfg(not(windows))]
fn full_scan(_pid: u32, _min_seq: i32) -> Option<(usize, Vec<u8>)> { None }

// ── Fast path: read at cached address ─────────────────────────────────────────

/// Read whatever is at the cached address, without deciding anything about it.
///
/// It used to answer "is there anything newer than min_seq here", which sounds
/// like the same question and is not: a copy the addon will never touch again
/// answers "no", and so does a chat where nobody is talking. Telling those two
/// apart is what the pulse is for, and that comparison belongs to the caller,
/// which is the only place that knows what pulse it saw last.
#[cfg(windows)]
fn try_read_at(handle: HANDLE, addr: usize) -> Option<Vec<u8>> {
    let mut raw = vec![0u8; MAX_BUF_READ];
    if !read_memory(handle, addr, &mut raw) {
        return None;
    }
    let cs = find_content_start(&raw)?;
    let ep = raw[cs..].windows(MARKER_END.len()).position(|w| w == MARKER_END)?;
    Some(raw[cs..cs + ep].to_vec())
}

#[cfg(not(windows))]
fn try_read_at(_handle: (), _addr: usize) -> Option<Vec<u8>> { None }

// ── Reading through the addon's table ────────────────────────────────────────

/// Read the buffer the slot currently points at.
#[cfg(windows)]
fn read_via_slot(handle: HANDLE, slot: usize, skip: usize) -> Option<Vec<u8>> {
    let mut pointer_bytes = [0u8; 8];
    if !read_memory(handle, slot, &mut pointer_bytes) {
        return None;
    }
    let pointer = u64::from_le_bytes(pointer_bytes) as usize;
    if pointer < 0x10000 {
        return None;
    }
    let mut raw = vec![0u8; MAX_BUF_READ];
    if !read_memory(handle, pointer + skip, &mut raw) {
        return None;
    }
    let cs = find_content_start(&raw)?;
    let ep = raw[cs..].windows(MARKER_END.len()).position(|w| w == MARKER_END)?;
    Some(raw[cs..cs + ep].to_vec())
}

/// Find the anchor, then the slot beside it. Slow, and done once.
#[cfg(windows)]
fn locate_anchor(pid: u32) -> Option<(usize, usize)> {
    let handle = open_process(pid)?;
    let needle = ANCHOR_VALUE.to_le_bytes();
    let regions = get_readable_regions(handle);

    let anchors: Mutex<Vec<usize>> = Mutex::new(Vec::new());
    get_pool().install(|| {
        regions.par_iter().for_each_init(
            || open_process(pid).map(|h| h.0 as isize),
            |h, region| {
                let Some(raw) = h else { return };
                let handle = HANDLE(*raw as *mut _);
                let mut offset = 0usize;
                let mut chunk = vec![0u8; CHUNK_SIZE];
                while offset < region.size {
                    let size = CHUNK_SIZE.min(region.size - offset);
                    let base = region.base + offset;
                    offset += size;
                    let buf = &mut chunk[..size];
                    if !read_memory(handle, base, buf) { continue; }
                    let mut at = 0usize;
                    while at + 8 <= buf.len() {
                        if buf[at..at + 8] == needle && (base + at) % 8 == 0 {
                            anchors.lock().unwrap().push(base + at);
                        }
                        at += 8;
                    }
                }
            },
        );
    });

    let anchors = anchors.into_inner().ok()?;

    // Every candidate, then the liveliest — not the first that looks right.
    //
    // A reload leaves the previous table in memory until the collector gets to
    // it, so there can be two of these, and the dead one answers every question
    // the same way the live one does. It cost a hundred and eighty seconds of
    // silence to learn that twice.
    let mut best: Option<((i64, i32), usize, usize)> = None;
    for anchor in anchors {
        let low = anchor.saturating_sub(ANCHOR_WINDOW);
        let mut window = vec![0u8; ANCHOR_WINDOW * 2];
        if !read_memory(handle, low, &mut window) { continue; }
        for offset in (0..window.len().saturating_sub(8)).step_by(8) {
            let pointer = u64::from_le_bytes(window[offset..offset + 8].try_into().ok()?) as usize;
            if pointer < 0x10000 || pointer > 0x7FFF_FFFF_FFFF { continue; }
            for skip in [32usize, 24, 16, 40, 48, 0] {
                let mut head = [0u8; 10];
                if !read_memory(handle, pointer + skip, &mut head) || head != *MARKER {
                    continue;
                }
                let slot = low + offset;
                if let Some(content) = read_via_slot(handle, slot, skip) {
                    let score = rank(&content);
                    if best.as_ref().is_none_or(|(b, _, _)| score > *b) {
                        best = Some((score, slot, skip));
                    }
                }
                break;
            }
        }
    }
    unsafe { let _ = CloseHandle(handle); }
    best.map(|(_, slot, skip)| (slot, skip))
}

#[cfg(not(windows))]
fn locate_anchor(_pid: u32) -> Option<(usize, usize)> { None }

// ── C export ─────────────────────────────────────────────────────────────────

/// Describe the scanner's own state, so a stall can be read rather than guessed.
///
/// Writes `cached=<0|1> addr=<hex> pulse=<n> quiet_ms=<n> scans=<n>` into
/// out_buf. Every hour spent on this scanner so far went into inferring these
/// five numbers from the outside.
#[unsafe(no_mangle)]
pub extern "C" fn describe_state(out_buf: *mut u8, out_len: i32) -> i32 {
    if out_buf.is_null() || out_len <= 0 { return -1; }

    #[cfg(windows)]
    let text = {
        let cache = CACHE.lock().unwrap();
        match *cache {
            Some(ref c) => format!(
                "cached=1 addr=0x{:x} pulse={} quiet_ms={} scans={}",
                c.addr,
                c.last_flush,
                now_ms().saturating_sub(c.last_fresh_ms),
                SCAN_COUNT.load(Ordering::Relaxed),
            ),
            None => format!("cached=0 scans={}", SCAN_COUNT.load(Ordering::Relaxed)),
        }
    };
    #[cfg(not(windows))]
    let text = String::from("cached=0 scans=0");

    #[cfg(windows)]
    let text = {
        let anchor = ANCHOR.lock().unwrap();
        match *anchor {
            Some(ref a) => format!("{text} slot=0x{:x}+{}", a.slot, a.skip),
            None => format!("{text} slot=none"),
        }
    };

    let bytes = text.as_bytes();
    let n = bytes.len().min(out_len as usize - 1);
    unsafe {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), out_buf, n);
        *out_buf.add(n) = 0;
    }
    n as i32
}

/// Find the WoW addon buffer and write its content into out_buf.
///
/// Fast path: single ReadProcessMemory at cached address (cached handle).
/// Returns: bytes written (>0) new data; 0 = buffer valid, nothing new
/// (steady idle state — NO scan); -1 = failure / scan rate-limited.
#[unsafe(no_mangle)]
pub extern "C" fn find_and_read_buffer(
    pid: i32,
    min_seq: i32,
    out_buf: *mut u8,
    out_len: i32,
) -> i32 {
    if out_buf.is_null() || out_len <= 0 { return -1; }
    let pid = pid as u32;

    // A negative min_seq means "do not believe the cached address" — scan.
    //
    // The caller needs this because the fast path cannot tell a ghost from a
    // live buffer, and asking it with min_seq = 0 makes matters worse rather
    // than better: at zero, ANY parseable bytes at the cached address look
    // fresh, so the question itself keeps the stale cache alive. That is not
    // hypothetical; it is what a five-second "have I run ahead?" probe did to
    // this scanner, holding a dead address for two minutes at a time.
    let forced = min_seq < 0;
    let min_seq = if forced { 0 } else { min_seq };
    if forced {
        #[cfg(windows)]
        {
            let mut cache = CACHE.lock().unwrap();
            if let Some(ref c) = *cache {
                unsafe { let _ = CloseHandle(HANDLE(c.handle as *mut _)); }
            }
            *cache = None;
        }
    }

    let write_out = |content: Vec<u8>| -> i32 {
        let pulse = extract_flush(&content);
        if pulse > LAST_PULSE.load(Ordering::Relaxed) {
            LAST_PULSE.store(pulse, Ordering::Relaxed);
        }
        let n = content.len().min(out_len as usize - 1);
        unsafe {
            std::ptr::copy_nonoverlapping(content.as_ptr(), out_buf, n);
            *out_buf.add(n) = 0;
        }
        n as i32
    };

    // ── The slot, if we have one ─────────────────────────────────────────────
    //
    // This is the whole scanner in three reads: eight bytes for the pointer,
    // the buffer at the far end of it, and the markers. Nothing is searched
    // for, so nothing costs anything, and the answer is the live buffer by
    // construction rather than by inference.
    #[cfg(windows)]
    {
        let mut anchor = ANCHOR.lock().unwrap();
        let mut lost = false;
        if let Some(ref mut a) = *anchor {
            if a.pid == pid {
                match read_via_slot(HANDLE(a.handle as *mut _), a.slot, a.skip) {
                    Some(content) => {
                        // The pulse ticks whether or not anyone is talking, so a
                        // slot whose pulse has stopped is pointing at a table
                        // nobody writes to any more — which is what a reload
                        // leaves behind. Waiting for the pointer to break
                        // instead took three minutes of silence.
                        let pulse = extract_flush(&content);
                        let now = now_ms();
                        if pulse > a.last_pulse {
                            a.last_pulse = pulse;
                            a.last_pulse_ms = now;
                        } else if now.saturating_sub(a.last_pulse_ms) > ANCHOR_MAX_QUIET_MS {
                            lost = true;
                        }
                        if !lost {
                            if extract_max_seq(&content) > min_seq {
                                return write_out(content);
                            }
                            return 0;
                        }
                    }
                    // A reload rebuilds the table and the slot moves with it.
                    None => lost = true,
                }
            } else {
                lost = true;
            }
        }
        if lost {
            if let Some(ref a) = *anchor {
                unsafe { let _ = CloseHandle(HANDLE(a.handle as *mut _)); }
            }
            *anchor = None;
        }
        if anchor.is_none() {
            if let Some((slot, skip)) = locate_anchor(pid) {
                if let Some(h) = open_process(pid) {
                    *anchor = Some(Anchor {
                        pid,
                        slot,
                        skip,
                        last_pulse: 0,
                        last_pulse_ms: now_ms(),
                        handle: h.0 as isize,
                    });
                    if let Some(content) = read_via_slot(h, slot, skip) {
                        if extract_max_seq(&content) > min_seq {
                            return write_out(content);
                        }
                        return 0;
                    }
                }
            }
        }
    }

    // ── Fast path ────────────────────────────────────────────────────────────
    #[cfg(windows)]
    if !forced {
        let mut cache = CACHE.lock().unwrap();
        let mut drop_cache = false;
        if let Some(ref mut c) = *cache {
            if c.pid == pid {
                let handle = HANDLE(c.handle as *mut _);
                match try_read_at(handle, c.addr) {
                    None => drop_cache = true, // unreadable or markers gone
                    Some(content) => {
                        let (flush, seq) = rank(&content);
                        // A pulse that moved means the addon wrote this copy,
                        // so hand it over and let the caller work out which of
                        // the lines it has already seen. An addon too old to
                        // have a pulse falls back to the message counter, which
                        // is what this did before and is still better than
                        // nothing.
                        let advanced = if flush > 0 { flush > c.last_flush } else { seq > min_seq };
                        if advanced {
                            c.last_flush = flush;
                            c.last_fresh_ms = now_ms();
                            return write_out(content);
                        }
                        // Nothing moved. Idle chat must not scan on every poll
                        // — that pegged the CPU — but with a pulse ticking
                        // every couple of seconds, silence this long is not
                        // idleness. It is the wrong address.
                        if now_ms().saturating_sub(c.last_fresh_ms) < CACHE_MAX_QUIET_MS {
                            return 0;
                        }
                        drop_cache = true;
                    }
                }
                if drop_cache {
                    unsafe { let _ = CloseHandle(handle); }
                }
            }
        }
        if drop_cache {
            *cache = None; // fall through to scan
        }
    }

    // ── Slow path (rate-limited, one at a time) ──────────────────────────────
    if SCAN_IN_PROGRESS.swap(true, Ordering::SeqCst) {
        return -1;
    }
    SCAN_COUNT.fetch_add(1, Ordering::Relaxed);

    // There was a tier here that looked in the regions the buffer had lived in
    // lately — the one the Python reader had, and the rewrite dropped. Putting
    // it back was measured rather than assumed, and the measurement killed it:
    // fourteen consecutive rebuilds landed in fourteen DIFFERENT regions,
    // scattered across twenty gigabytes of address space. The buffer never goes
    // back where it was, so the tier missed every time and cost a scan of
    // fourteen megabytes before the real sweep even started — 180% of a core.
    //
    // What worked for the Python reader was that its regions were remembered
    // across a slower flush interval. What is true now is that a Lua string is
    // reallocated somewhere new on every rebuild, and nothing local to the last
    // address helps.
    let now = now_ms();
    let last = LAST_SCAN_MS.load(Ordering::Relaxed);
    if !forced && now.saturating_sub(last) < SCAN_MIN_GAP_MS {
        SCAN_IN_PROGRESS.store(false, Ordering::SeqCst);
        return -1;
    }
    LAST_SCAN_MS.store(now, Ordering::Relaxed);
    let result = full_scan(pid, min_seq);
    SCAN_IN_PROGRESS.store(false, Ordering::SeqCst);

    match result {
        None => -1,
        Some((addr, content)) => {
            #[cfg(windows)]
            {
                // Cache a persistent handle alongside the address.
                if let Some(h) = open_process(pid) {
                    *CACHE.lock().unwrap() =
                        Some(Cache { pid, addr, handle: h.0 as isize, last_flush: rank(&content).0, last_fresh_ms: now_ms() });
                }
            }
            write_out(content)
        }
    }
}
