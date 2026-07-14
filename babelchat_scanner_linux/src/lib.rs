/// babelchat_scanner — Linux memory scanner for BabelChat companion app.
///
/// Uses process_vm_readv instead of /proc/<pid>/mem for cross-process reads.
/// process_vm_readv is designed for debuggers/profilers and causes significantly
/// less disruption to the target process than the /proc/mem approach — no VFS
/// overhead, no file descriptor, no kernel page fault handling detour.
///
/// Fast path: single process_vm_readv at cached address (~microseconds).
/// Slow path: parallel region scan on cache miss (~every 14s on GC relocation).
extern crate libc;

use libc::{iovec, pid_t, process_vm_readv};
use rayon::prelude::*;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

const MARKER: &[u8] = b"__WCT_BUF_";
const MARKER_LEGACY: &[u8] = b"__WCT_BUF__";
const MARKER_END: &[u8] = b"__WCT_END__";
const CHUNK_SIZE: usize = 65536;
const MAX_BUF_READ: usize = 65536;
const MAX_REGION_SIZE: u64 = 128 * 1024 * 1024;

// ── Global state ──────────────────────────────────────────────────────────────

struct Cache {
    pid: i32,
    addr: u64,
}

static CACHE: Mutex<Option<Cache>> = Mutex::new(None);
static SCAN_IN_PROGRESS: AtomicBool = AtomicBool::new(false);
/// Minimum gap between full scans (ms) — prevents scan loops if the cache is
/// lost while chat is idle (e.g. after an in-game /reload).
static LAST_SCAN_MS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
const SCAN_MIN_GAP_MS: u64 = 3000;

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}
static POOL: std::sync::OnceLock<rayon::ThreadPool> = std::sync::OnceLock::new();

fn get_pool() -> &'static rayon::ThreadPool {
    POOL.get_or_init(|| {
        rayon::ThreadPoolBuilder::new()
            .num_threads(2)
            .thread_name(|i| format!("babelchat-scan-{}", i))
            .start_handler(|_| unsafe {
                // SCHED_IDLE: only runs when nothing else needs CPU
                libc::setpriority(libc::PRIO_PROCESS, 0, 19);
                let param = libc::sched_param { sched_priority: 0 };
                libc::sched_setscheduler(0, libc::SCHED_IDLE, &param);
            })
            .build()
            .expect("failed to build rayon pool")
    })
}

// ── process_vm_readv wrapper ──────────────────────────────────────────────────

/// Read `size` bytes from `pid` at virtual address `addr` into `buf`.
/// Returns true on success.
fn vm_read(pid: pid_t, addr: u64, buf: &mut [u8]) -> bool {
    let local = iovec {
        iov_base: buf.as_mut_ptr() as *mut libc::c_void,
        iov_len: buf.len(),
    };
    let remote = iovec {
        iov_base: addr as *mut libc::c_void,
        iov_len: buf.len(),
    };
    let ret = unsafe {
        process_vm_readv(pid as pid_t, &local, 1, &remote, 1, 0)
    };
    ret == buf.len() as isize
}

/// Read up to `size` bytes, returning however many were read.
fn vm_read_partial(pid: pid_t, addr: u64, size: usize) -> Option<Vec<u8>> {
    let mut buf = vec![0u8; size];
    let local = iovec {
        iov_base: buf.as_mut_ptr() as *mut libc::c_void,
        iov_len: size,
    };
    let remote = iovec {
        iov_base: addr as *mut libc::c_void,
        iov_len: size,
    };
    let ret = unsafe {
        process_vm_readv(pid as pid_t, &local, 1, &remote, 1, 0)
    };
    if ret <= 0 {
        return None;
    }
    buf.truncate(ret as usize);
    Some(buf)
}

// ── Region parsing ────────────────────────────────────────────────────────────

#[derive(Clone)]
struct Region {
    base: u64,
    size: u64,
}

fn parse_maps(pid: i32) -> std::io::Result<Vec<Region>> {
    let content = std::fs::read_to_string(format!("/proc/{}/maps", pid))?;
    let mut regions = Vec::with_capacity(256);

    for line in content.lines() {
        let mut parts = line.splitn(6, ' ');
        let addr_range = match parts.next() { Some(s) => s, None => continue };
        let perms      = match parts.next() { Some(s) => s, None => continue };
        if !perms.starts_with('r') { continue; }
        let name = parts.nth(3).unwrap_or("").trim();
        if matches!(name, "[vvar]" | "[vdso]" | "[vsyscall]") { continue; }

        let mut a = addr_range.splitn(2, '-');
        let start = match a.next().and_then(|s| u64::from_str_radix(s, 16).ok()) { Some(v) => v, None => continue };
        let end   = match a.next().and_then(|s| u64::from_str_radix(s, 16).ok()) { Some(v) => v, None => continue };
        if end <= start { continue; }
        let size = end - start;
        if size == 0 || size > MAX_REGION_SIZE { continue; }
        regions.push(Region { base: start, size });
    }

    // Smallest-first — active Lua strings live in smaller allocations
    regions.sort_unstable_by_key(|r| r.size);
    Ok(regions)
}

// ── Marker / content helpers ──────────────────────────────────────────────────

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

fn try_read_at(pid: i32, addr: u64, min_seq: i32) -> Option<Vec<u8>> {
    let raw = vm_read_partial(pid, addr, MAX_BUF_READ)?;
    let cs = find_content_start(&raw)?;
    let ep = raw[cs..].windows(MARKER_END.len()).position(|w| w == MARKER_END)?;
    let content = &raw[cs..cs + ep];
    if extract_max_seq(content) > min_seq {
        Some(content.to_vec())
    } else {
        None
    }
}

/// Outcome of a fast-path read at the cached address.
enum FastRead {
    Fresh(Vec<u8>),
    /// Buffer valid at cached address but nothing newer than min_seq.
    /// Must NOT trigger a rescan — this is the steady idle state.
    NoNew,
    /// Read failed or markers gone: cache stale, rescan needed.
    Stale,
}

fn fast_read_at(pid: i32, addr: u64, min_seq: i32) -> FastRead {
    let Some(raw) = vm_read_partial(pid, addr, MAX_BUF_READ) else { return FastRead::Stale };
    let Some(cs) = find_content_start(&raw) else { return FastRead::Stale };
    let Some(ep) = raw[cs..].windows(MARKER_END.len()).position(|w| w == MARKER_END) else {
        return FastRead::Stale;
    };
    let content = &raw[cs..cs + ep];
    if extract_max_seq(content) > min_seq {
        FastRead::Fresh(content.to_vec())
    } else {
        FastRead::NoNew
    }
}

// ── Region scan ───────────────────────────────────────────────────────────────

fn scan_region(pid: i32, region: &Region, min_seq: i32) -> Option<(u64, Vec<u8>)> {
    let mut offset = 0u64;
    let mut chunk_buf = vec![0u8; CHUNK_SIZE];

    while offset < region.size {
        let chunk_size = CHUNK_SIZE.min((region.size - offset) as usize);
        let chunk_addr = region.base + offset;
        offset += chunk_size as u64;

        let buf = &mut chunk_buf[..chunk_size];
        if !vm_read(pid, chunk_addr, buf) { continue; }

        // Fast check: marker present in chunk?
        if !buf.windows(MARKER.len()).any(|w| w == MARKER) { continue; }

        let mut search = 0;
        while search < buf.len() {
            match buf[search..].windows(MARKER.len()).position(|w| w == MARKER) {
                None => break,
                Some(rel) => {
                    let marker_addr = chunk_addr + (search + rel) as u64;
                    search += rel + MARKER.len();
                    if let Some(content) = try_read_at(pid, marker_addr, min_seq) {
                        return Some((marker_addr, content));
                    }
                }
            }
        }
    }
    None
}

// ── Full parallel scan ────────────────────────────────────────────────────────

fn full_scan(pid: i32, min_seq: i32) -> Option<(u64, Vec<u8>)> {
    let regions = parse_maps(pid).ok()?;
    let found = AtomicBool::new(false);
    let result: Mutex<Option<(u64, Vec<u8>)>> = Mutex::new(None);

    get_pool().install(|| {
        regions.par_iter().for_each(|region| {
            if found.load(Ordering::Relaxed) { return; }
            if let Some((addr, content)) = scan_region(pid, region, min_seq) {
                if !found.swap(true, Ordering::Relaxed) {
                    *result.lock().unwrap() = Some((addr, content));
                }
            }
        });
    });

    result.into_inner().ok()?
}

// ── C export ─────────────────────────────────────────────────────────────────

#[unsafe(no_mangle)]
pub extern "C" fn find_and_read_buffer(
    pid: i32,
    min_seq: i32,
    out_buf: *mut u8,
    out_len: i32,
) -> i32 {
    if out_buf.is_null() || out_len <= 0 { return -1; }

    let write_out = |content: Vec<u8>| -> i32 {
        let n = content.len().min(out_len as usize - 1);
        unsafe {
            std::ptr::copy_nonoverlapping(content.as_ptr(), out_buf, n);
            *out_buf.add(n) = 0;
        }
        n as i32
    };

    // ── Fast path: cached address (single process_vm_readv) ──────────────────
    {
        let mut cache = CACHE.lock().unwrap();
        if let Some(ref c) = *cache {
            if c.pid == pid {
                match fast_read_at(pid, c.addr, min_seq) {
                    FastRead::Fresh(content) => return write_out(content),
                    // Idle (no new chat) must NOT fall through to a full scan —
                    // that pegged the CPU on every poll with no new messages.
                    FastRead::NoNew => return 0,
                    FastRead::Stale => { *cache = None; }
                }
            }
        }
    }

    // ── Slow path: full scan (rate-limited, one at a time) ───────────────────
    let now = now_ms();
    if now.saturating_sub(LAST_SCAN_MS.load(Ordering::Relaxed)) < SCAN_MIN_GAP_MS {
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
            *CACHE.lock().unwrap() = Some(Cache { pid, addr });
            write_out(content)
        }
    }
}
