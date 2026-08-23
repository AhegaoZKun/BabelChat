/// babelchat_scanner_win — Windows memory scanner for BabelChat.
///
/// Uses ReadProcessMemory + VirtualQueryEx to scan WoW process memory.
/// Same interface as the Linux scanner: one C export, address cache for
/// near-zero CPU in steady state.
///
/// Fast path: single ReadProcessMemory at cached address (~microseconds).
/// Slow path: parallel region scan on cache miss.
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

#[cfg(windows)]
use windows::Win32::Foundation::{CloseHandle, HANDLE};


mod anchor;
mod markers;
mod process;
mod scan;

use anchor::{locate_anchor, read_via_slot, Anchor, ANCHOR, ANCHOR_MAX_QUIET_MS};
use scan::full_scan;

use markers::{extract_flush, extract_max_seq, find_content_start, rank, MARKER_END, LAST_PULSE};
use process::{now_ms, open_process, read_memory, MAX_BUF_READ};

// ── Global state ──────────────────────────────────────────────────────────────

static CACHE: Mutex<Option<Cache>> = Mutex::new(None);
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

static SCAN_IN_PROGRESS: AtomicBool = AtomicBool::new(false);
/// Rate-limit full scans: if the cache is lost while chat is idle (e.g. after
/// a /reload), scans would otherwise repeat every poll. Minimum gap in ms.
static LAST_SCAN_MS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
const SCAN_MIN_GAP_MS: u64 = 3000;

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





/// How many full scans this process has run. Diagnostic only.
static SCAN_COUNT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

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
