"""The wire format between the WoW addon and the companion app.

The addon keeps a ring buffer inside a SavedVariable string and the companion
reads that string straight out of the game process. Both ends must agree on the
framing, and until now both ends of the *reader* — the Windows one and the Linux
one — carried their own copy of the parsing rules. They had already drifted
apart by a few comment lines, and a fix applied to one silently missed the other.

The format, one record per line:

    __WCT_BUF_NNNN__
    0|META|PLAYER|Name-Realm
    17|RAW|SAY|Thrall-Sargeras|hello everyone
    18|DICT|CHANNEL:Trade|Vasya|wts crest
    __WCT_END__

`NNNN` is the sequence counter modulo 10000 — cosmetic padding for a quick
staleness check; the authoritative sequence is the unbounded number that opens
each record. `KIND` is RAW or DICT, where DICT means the addon also glossed the
line in the player's own chat window. Fields are sanitised by the addon before
they get here: no record can contain a newline, a tab, or the frame markers.
"""

from __future__ import annotations

import re
import time

MARKER_START = b"__WCT_BUF_"
MARKER_START_LEGACY = b"__WCT_BUF__"
MARKER_END = b"__WCT_END__"

# Addon event token → the channel name the log parser expects.
_ADDON_CHANNEL_TO_LOG = {
    "SAY": "Say",
    "YELL": "Yell",
    "PARTY": "Party",
    "PARTY_LEADER": "Party Leader",
    "RAID": "Raid",
    "RAID_LEADER": "Raid Leader",
    "RAID_WARNING": "Raid Warning",
    "GUILD": "Guild",
    "OFFICER": "Officer",
    "INSTANCE_CHAT": "Instance",
    "INSTANCE_CHAT_LEADER": "Instance Leader",
    "CHANNEL": "Say",
    "EMOTE": "Say",
    "BATTLEGROUND": "Instance",
    "BATTLEGROUND_LEADER": "Instance Leader",
}

_LEADING_CLOCK = re.compile(r"^\d{1,2}:\d{2}:\d{2}\s+")

_NOISE_PREFIXES = ("<DBM>", "<BW>", "<WA>", "|TInterface", "[WCT]", "[MoveAny")
_NOISE_PHRASES = ("has earned", "achievement", "creates:", "создает:")


def is_system_noise(text: str) -> bool:
    """True for addon chatter and achievement spam — never worth translating."""
    stripped = _LEADING_CLOCK.sub("", text.lstrip())
    if stripped.startswith(_NOISE_PREFIXES):
        return True
    if "|Hachievement:" in stripped:
        return True
    return any(phrase in stripped for phrase in _NOISE_PHRASES)


def find_content_start(raw: bytes) -> int:
    """Offset of the first record, or -1 if `raw` does not open a buffer."""
    if raw.startswith(MARKER_START):
        end = raw.find(b"__", len(MARKER_START))
        if end != -1:
            return end + 2
    if raw.startswith(MARKER_START_LEGACY):
        return len(MARKER_START_LEGACY)
    return -1


def extract_max_seq(content: bytes) -> int:
    """Highest sequence number in a buffer payload; 0 if there is none.

    Used to pick the freshest buffer when a scan turns up several — a stale copy
    of the string can linger in the heap after the addon reallocates it.
    """
    max_seq = 0
    for line in content.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        idx = line.find(b"|")
        if idx <= 0:
            continue
        try:
            seq = int(line[:idx])
        except ValueError:
            continue
        if seq > max_seq:
            max_seq = seq
    return max_seq


def _classify_public_channel(name: str) -> str:
    """Map a public channel's display name onto a log channel.

    Matching is on the English name, so a localised client falls through to
    General — a known defect, tracked separately. It lives here rather than in
    two reader copies so the fix lands once.
    """
    lowered = name.strip().lower()
    if "trade" in lowered:
        return "Trade"
    if "service" in lowered or "comercio" in lowered:
        return "Services"
    if "lookingforgroup" in lowered or "looking for group" in lowered or "lfg" in lowered:
        return "LookingForGroup"
    return "General"


def _timestamp() -> str:
    t = time.localtime()
    return f"{t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.000"


def make_synthetic_log_line(channel: str, author: str, text: str) -> str | None:
    """Render a record as the WoW chat-log line the parser already understands.

    Returns None for an event token we do not recognise, which the caller treats
    as "deliver the bare text" rather than inventing a channel.
    """
    if channel.startswith("CHANNEL:"):
        log_channel = _classify_public_channel(channel.split(":", 1)[1])
        # Author can be empty on some channel messages; a placeholder keeps the
        # line in the parser's "[Channel] Author: text" shape instead of it
        # being dropped for looking malformed.
        who = author or "Unknown"
        return f"{_timestamp()}  [{log_channel}] {who}: {text}"

    if channel in ("WHISPER", "BN_WHISPER"):
        return f"{_timestamp()}  [{author}] whispers: {text}"
    if channel == "WHISPER_INFORM":
        return f"{_timestamp()}  To [{author}]: {text}"

    log_channel = _ADDON_CHANNEL_TO_LOG.get(channel)
    if log_channel is None:
        return None
    who = author or "Unknown"
    return f"{_timestamp()}  [{log_channel}] {who}: {text}"


def bare_log_line(text: str) -> str:
    """A timestamped line with no channel — the fallback when a record has no event."""
    return f"{_timestamp()}  {text}"
