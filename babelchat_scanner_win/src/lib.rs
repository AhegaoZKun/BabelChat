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
        Threading::{OpenProcess, PROCESS_VM_READ, PROCESS_QUERY_INFORMATION},
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
const CACHE_MAX_QUIET_MS: u64 = 10_000;
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

// ── Per-region scan ───────────────────────────────────────────────────────────

#[cfg(windows)]
fn scan_region(handle: HANDLE, region: &Region, min_seq: i32) -> Option<(usize, Vec<u8>)> {
    let mut offset = 0usize;
    let mut chunk_buf = vec![0u8; CHUNK_SIZE];

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
                    let seq = extract_max_seq(content);
                    if seq > min_seq {
                        return Some((marker_addr, content.to_vec()));
                    }
                }
            }
        }
    }
    None
}

// ── Full parallel scan ────────────────────────────────────────────────────────

#[cfg(windows)]
fn full_scan(pid: u32, min_seq: i32) -> Option<(usize, Vec<u8>)> {
    let handle = open_process(pid)?;
    let regions = get_readable_regions(handle);

    let found = AtomicBool::new(false);
    let result: Mutex<Option<(usize, Vec<u8>)>> = Mutex::new(None);

    // We need one handle per thread — duplicate it for each worker
    get_pool().install(|| {
        // One handle per worker split (for_each_init) instead of one
        // OpenProcess/CloseHandle per region — regions number in the
        // thousands; handles are expensive kernel objects.
        regions.par_iter().for_each_init(
            || open_process(pid).map(|h| h.0 as isize),
            |h, region| {
                if found.load(Ordering::Relaxed) { return; }
                let Some(raw) = h else { return };
                let handle = HANDLE(*raw as *mut _);
                if let Some((addr, content)) = scan_region(handle, region, min_seq) {
                    if !found.swap(true, Ordering::Relaxed) {
                        *result.lock().unwrap() = Some((addr, content));
                    }
                }
            },
        );
    });

    unsafe { let _ = CloseHandle(handle); }
    result.into_inner().ok()?
}

#[cfg(not(windows))]
fn full_scan(_pid: u32, _min_seq: i32) -> Option<(usize, Vec<u8>)> { None }

// ── Fast path: read at cached address ─────────────────────────────────────────

/// Outcome of a fast-path read at the cached address.
enum FastRead {
    /// Buffer valid, new messages present.
    Fresh(Vec<u8>),
    /// Buffer valid at cached address but nothing newer than min_seq.
    /// This must NOT trigger a rescan — it's the steady idle state.
    NoNew,
    /// Read failed or markers gone: cache is stale, rescan needed.
    Stale,
}

#[cfg(windows)]
fn try_read_at(handle: HANDLE, addr: usize, min_seq: i32) -> FastRead {
    let mut raw = vec![0u8; MAX_BUF_READ];
    if !read_memory(handle, addr, &mut raw) {
        return FastRead::Stale;
    }
    let cs = match find_content_start(&raw) {
        Some(cs) => cs,
        None => return FastRead::Stale,
    };
    let ep = match raw[cs..].windows(MARKER_END.len()).position(|w| w == MARKER_END) {
        Some(p) => p,
        None => return FastRead::Stale,
    };
    let content = &raw[cs..cs + ep];
    if extract_max_seq(content) > min_seq {
        FastRead::Fresh(content.to_vec())
    } else {
        FastRead::NoNew
    }
}

#[cfg(not(windows))]
fn try_read_at(_handle: (), _addr: usize, _min_seq: i32) -> FastRead { FastRead::Stale }

// ── C export ─────────────────────────────────────────────────────────────────

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

    let write_out = |content: Vec<u8>| -> i32 {
        let n = content.len().min(out_len as usize - 1);
        unsafe {
            std::ptr::copy_nonoverlapping(content.as_ptr(), out_buf, n);
            *out_buf.add(n) = 0;
        }
        n as i32
    };

    // ── Fast path ────────────────────────────────────────────────────────────
    #[cfg(windows)]
    {
        let mut cache = CACHE.lock().unwrap();
        if let Some(ref mut c) = *cache {
            if c.pid == pid {
                let handle = HANDLE(c.handle as *mut _);
                match try_read_at(handle, c.addr, min_seq) {
                    FastRead::Fresh(content) => {
                        c.last_fresh_ms = now_ms();
                        return write_out(content);
                    }
                    // Idle chat must not fall through to a full scan on every
                    // poll — that pegged the CPU. But it must not be trusted
                    // for ever either, because a ghost buffer is indis-
                    // tinguishable from an idle one from here. After
                    // CACHE_MAX_QUIET_MS with nothing new, verify by scanning;
                    // the scan is rate-limited, so the cost of a genuinely
                    // quiet chat is one scan every few seconds, not one per
                    // poll.
                    FastRead::NoNew => {
                        if now_ms().saturating_sub(c.last_fresh_ms) < CACHE_MAX_QUIET_MS {
                            return 0;
                        }
                        unsafe { let _ = CloseHandle(handle); }
                        *cache = None; // fall through to scan
                    }
                    FastRead::Stale => {
                        unsafe { let _ = CloseHandle(handle); }
                        *cache = None; // fall through to scan
                    }
                }
            }
        }
    }

    // ── Slow path (rate-limited, one at a time) ──────────────────────────────
    let now = now_ms();
    let last = LAST_SCAN_MS.load(Ordering::Relaxed);
    if now.saturating_sub(last) < SCAN_MIN_GAP_MS {
        return -1;
    }
    if SCAN_IN_PROGRESS.swap(true, Ordering::SeqCst) {
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
                        Some(Cache { pid, addr, handle: h.0 as isize, last_fresh_ms: now_ms() });
                }
            }
            write_out(content)
        }
    }
}
