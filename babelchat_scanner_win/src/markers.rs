//! What a companion buffer looks like in memory, and how alive a copy of one
//! is.
//!
//! Two numbers answer the second question and the order matters. The pulse is
//! the addon's rebuild counter and it ticks whether or not anyone is talking,
//! so a copy whose pulse has stopped is one the addon will never write to
//! again. The message sequence only records what was last said — which a copy
//! holding the last thing said answers exactly as well, and that confusion is
//! what left the reader deaf for minutes at a time.

pub(crate) const MARKER: &[u8] = b"__WCT_BUF_";
const MARKER_LEGACY: &[u8] = b"__WCT_BUF__";
pub(crate) const MARKER_END: &[u8] = b"__WCT_END__";

// ── Marker helpers ────────────────────────────────────────────────────────────

pub(crate) fn find_content_start(raw: &[u8]) -> Option<usize> {
    if raw.starts_with(b"__WCT_BUF_") {
        if let Some(p) = raw[10..].windows(2).position(|w| w == b"__") {
            return Some(10 + p + 2);
        }
    }
    if raw.starts_with(MARKER_LEGACY) { return Some(MARKER_LEGACY.len()); }
    None
}

pub(crate) fn extract_max_seq(content: &[u8]) -> i32 {
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
pub(crate) fn extract_flush(content: &[u8]) -> i64 {
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
pub(crate) fn rank(content: &[u8]) -> (i64, i32) {
    (extract_flush(content), extract_max_seq(content))
}


/// The highest pulse ever seen in a live buffer.
///
/// A copy left behind by an earlier rebuild cannot have a pulse above this — it
/// stopped being written before the number got here. So a candidate that beats
/// it is the live buffer and the scan can stop, which is what makes an early
/// exit correct. The old early exit compared message counters instead, and a
/// corpse holding the last message ever written matches that just as well.
pub(crate) static LAST_PULSE: std::sync::atomic::AtomicI64 = std::sync::atomic::AtomicI64::new(0);

