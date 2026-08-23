//! Searching the game's memory for a companion buffer.
//!
//! The last resort, and it used to be the only resort. The buffer is a Lua
//! string, so every rebuild puts it somewhere else — fourteen rebuilds, fourteen
//! regions, twenty gigabytes apart — and a sweep per rebuild is what made the
//! previous release cost 48% of one core. What runs now is the addon's own table
//! slot (see anchor.rs); this is for an addon too old to have one, and for
//! finding the slot in the first place.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

#[cfg(windows)]
use rayon::prelude::*;
#[cfg(windows)]
use windows::Win32::Foundation::{CloseHandle, HANDLE};

use crate::markers::{find_content_start, rank, MARKER, MARKER_END, LAST_PULSE};
use crate::process::{get_pool, open_process, read_memory, Region, CHUNK_SIZE, MAX_BUF_READ};
#[cfg(windows)]
use crate::process::get_readable_regions;

// ── Per-region scan ───────────────────────────────────────────────────────────

#[cfg(windows)]
pub(crate) fn scan_region(handle: HANDLE, region: &Region, _min_seq: i32) -> Option<(usize, Vec<u8>)> {
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
pub(crate) fn full_scan(pid: u32, min_seq: i32) -> Option<(usize, Vec<u8>)> {
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
pub(crate) fn full_scan(_pid: u32, _min_seq: i32) -> Option<(usize, Vec<u8>)> { None }

