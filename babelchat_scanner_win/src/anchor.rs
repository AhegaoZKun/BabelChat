//! Reading the buffer through the addon's own table, instead of hunting for it.
//!
//! The addon parks a constant in its saved table. A constant can be searched for
//! at leisure — it does not move while you look — and a Lua table's storage does
//! not move at all while the table does not rehash, which the addon prevents by
//! declaring every key at load. So the slot holding the buffer's pointer sits
//! beside that constant, and reading eight bytes gives the current string
//! whatever address it has wandered to.
//!
//! Proven on a live game before any of it was written: one slot, six reads over
//! twelve seconds, six different string addresses, every one a valid buffer.

use std::sync::Mutex;

#[cfg(windows)]
use rayon::prelude::*;
#[cfg(windows)]
use windows::Win32::Foundation::{CloseHandle, HANDLE};

use crate::markers::{find_content_start, rank, MARKER, MARKER_END};
#[cfg(windows)]
use crate::process::{get_pool, get_readable_regions, open_process, read_memory, CHUNK_SIZE, MAX_BUF_READ};

/// How long a cached address may produce nothing before it is checked by a
/// scan. Long enough that an ordinary lull in chat costs nothing, short enough
/// that a relocated buffer is found while the player is still looking at the
/// message they expected to see translated.
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
pub(crate) const ANCHOR_MAX_QUIET_MS: u64 = 6_000;

/// How far either side of the anchor to look for that slot. The proof found it
/// 112 bytes below.
const ANCHOR_WINDOW: usize = 8192;

pub(crate) struct Anchor {
    pub(crate) pid: u32,
    /// The pulse last read through this slot, and when. A reload leaves the old
    /// table in memory with its buffer frozen at the last thing said before it,
    /// and that reads exactly like a working slot in a quiet chat — the same
    /// confusion the buffer itself caused, one level up.
    pub(crate) last_pulse: i64,
    pub(crate) last_pulse_ms: u64,
    /// Address of the eight bytes holding the pointer to the buffer string.
    pub(crate) slot: usize,
    /// Distance from that pointer to the text — the Lua string header.
    pub(crate) skip: usize,
    pub(crate) handle: isize,
}

pub(crate) static ANCHOR: Mutex<Option<Anchor>> = Mutex::new(None);

// ── Reading through the addon's table ────────────────────────────────────────

/// Read the buffer the slot currently points at.
#[cfg(windows)]
pub(crate) fn read_via_slot(handle: HANDLE, slot: usize, skip: usize) -> Option<Vec<u8>> {
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
pub(crate) fn locate_anchor(pid: u32) -> Option<(usize, usize)> {
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
pub(crate) fn locate_anchor(_pid: u32) -> Option<(usize, usize)> { None }

