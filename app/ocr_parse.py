"""Turn OCR output into the chat-log lines the pipeline already understands.

The screen-capture source has no access to the game, only to pixels, so
everything the addon buffer hands over for free — which channel, who spoke,
where one message ends and the next begins — has to be recovered from text
that is rendered, wrapped and scrolled by the game's chat frame. This module is
that recovery, and it is deliberately free of any capture or OCR dependency so
it can be tested with plain strings.

The stages, in the order a frame passes through them:

    boxes_to_lines     recognised boxes   -> one string per visual line
    frame_from_lines   visual lines       -> logical entries (wrapped lines joined)
    ChatDiffer.update  entry list         -> only the entries that are new
    entry.msg          screen message     -> make_synthetic_log_line(...)

Channel and verb vocabulary is best-effort for the five client languages the app
translates between; anything it does not recognise degrades to a Custom channel
or is dropped, never guessed into a channel the user may have switched off.
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher, get_close_matches

from app.addon_protocol import make_synthetic_log_line

#: A recognised text box: (x0, y0, x1, y1, text, confidence).
Box = tuple[float, float, float, float, str, float]

# ── vocabulary ───────────────────────────────────────────────────────────────

# Group channels by the name the client prints inside the brackets, lowercased.
# Value is the addon event token make_synthetic_log_line() understands.
_GROUP_CHANNELS: dict[str, str] = {}


def _register(token: str, *names: str) -> None:
    for name in names:
        _GROUP_CHANNELS[name] = token


_register("GUILD", "guild", "гильдия", "hermandad", "gilde", "guilde", "gilda")
_register("OFFICER", "officer", "офицеры", "офицер", "oficial", "offizier", "officier", "ufficiale")
_register("PARTY", "party", "группа", "grupo", "gruppe", "groupe", "gruppo")
_register(
    "PARTY_LEADER",
    "party leader", "лидер группы", "líder de grupo", "lider de grupo",
    "gruppenleiter", "chef de groupe", "capogruppo",
)
_register("RAID", "raid", "рейд", "banda", "schlachtzug", "incursione")
_register(
    "RAID_LEADER",
    "raid leader", "лидер рейда", "líder de banda", "lider de banda",
    "schlachtzugsleiter", "chef de raid", "capo incursione",
)
_register(
    "RAID_WARNING",
    "raid warning", "объявление рейду", "aviso de banda",
    "schlachtzugswarnung", "avertissement de raid", "avviso incursione",
)
_register("INSTANCE_CHAT", "instance", "instance chat", "подземелье", "instancia", "instanz")
_register(
    "INSTANCE_CHAT_LEADER",
    "instance leader", "лидер подземелья", "líder de instancia", "lider de instancia",
    "instanzleiter", "chef d'instance",
)
_register("BATTLEGROUND", "battleground", "поле боя", "campo de batalla", "schlachtfeld", "champ de bataille")
_register(
    "BATTLEGROUND_LEADER",
    "battleground leader", "лидер поля боя", "líder de campo de batalla", "schlachtfeldleiter",
)

# Built-in public channels by the name the client prints, lowercased:
# name -> (canonical English name, WoW zoneChannelID or None when it has none we
# know). Only these are ever classified as Trade/General/...; anything else
# numbered is a channel a player made, whatever it happens to be called. That is
# the same rule the addon path follows, and it is why "[5. TradeHub]" must not
# end up in Trade.
_PUBLIC_CHANNELS: dict[str, tuple[str, int | None]] = {}


def _public(canonical: str, type_id: int | None, *names: str) -> None:
    for name in names:
        _PUBLIC_CHANNELS[name] = (canonical, type_id)


_public("Trade", 2, "trade", "торговля", "handel", "commerce", "comercio", "commercio", "comércio")
_public("Services", None, "services", "service", "услуги", "servicios", "servizi", "serviços", "dienste")
_public(
    "LookingForGroup", 26,
    "lookingforgroup", "looking for group", "lfg", "поиск группы", "поиск спутников",
    "buscar grupo", "recherche de groupe", "suche nach gruppe",
)
_public("General", 1, "general", "общий", "allgemein", "général", "generale", "geral")

_WHISPER_FROM_VERBS = r"whispers|шепчет|susurra|flüstert|chuchote|sussurra|sussurrando"
_WHISPER_TO_LEADS = r"To|Кому"
_WHISPER_TO_LEADS_BRACKETED = r"A|An|À|Para|Per"
_SAY_VERBS: dict[str, str] = {
    "says": "SAY", "говорит": "SAY", "dice": "SAY", "sagt": "SAY", "dit": "SAY", "diz": "SAY",
    "yells": "YELL", "кричит": "YELL", "grita": "YELL", "schreit": "YELL", "crie": "YELL", "grida": "YELL",
}

# An optional leading clock, as printed when the user turns chat timestamps on:
# "[12:34]", "12:34:56", "[12:34 PM]".
_CLOCK = r"(?:\[?\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?\]?\s+)?"

# Names: letters, digits, apostrophes, hyphen for the realm. No spaces, which is
# also what keeps NPC lines ("High King Anduin says: ...") out — the pipeline
# drops them anyway, so recognising them would only cost work.
_NAME = r"[^\s:;\[\]()<>]+?"
_NAME_BRACKETED = r"\[?(?P<author>" + _NAME + r")(?:-(?P<realm>[^\s:;\[\]]+))?\]?"
# OCR reads a colon as a semicolon often enough to be worth accepting.
_COLON = r"\s*[:;]\s*"

_RE_CHANNEL = re.compile(
    r"^" + _CLOCK + r"\[(?P<chan>[^\]]+)\]\s*" + _NAME_BRACKETED + _COLON + r"(?P<text>\S.*)$"
)
_RE_WHISPER_FROM = re.compile(
    r"^" + _CLOCK + _NAME_BRACKETED + r"\s+(?:" + _WHISPER_FROM_VERBS + r")" + _COLON + r"(?P<text>\S.*)$",
    re.IGNORECASE,
)
_RE_WHISPER_TO = re.compile(
    r"^" + _CLOCK + r"(?:" + _WHISPER_TO_LEADS + r")\s+" + _NAME_BRACKETED + _COLON + r"(?P<text>\S.*)$"
)
_RE_WHISPER_TO_BRACKETED = re.compile(
    r"^" + _CLOCK + r"(?:" + _WHISPER_TO_LEADS_BRACKETED + r")\s+"
    r"\[(?P<author>" + _NAME + r")(?:-(?P<realm>[^\s:;\[\]]+))?\]"
    + _COLON + r"(?P<text>\S.*)$"
)
_RE_SAY_YELL = re.compile(
    r"^" + _CLOCK + _NAME_BRACKETED + r"\s+(?P<verb>" + "|".join(_SAY_VERBS) + r")" + _COLON + r"(?P<text>\S.*)$",
    re.IGNORECASE,
)

# Entries that are visibly their own line but not chat: the continuation rule
# below must not glue them onto the message above them.
_RE_NEW_ENTRY_HINT = re.compile(
    r"^" + _CLOCK + r"(?:"
    r"you\b|вы\b|ты\b|tú\b|usted\b|"
    r"loot\b|quest\b|reputation\b|experience\b|"
    r"[^\s]+ (?:has|have) (?:come online|gone offline|joined|left)|"
    r"[^\s]+ (?:присоединил|покинул|входит|выходит)"
    r")",
    re.IGNORECASE,
)

# The GTK overlay renders "original  →  translation". If the capture region
# overlaps the overlay, its own rows must never be read back as chat.
_OWN_OVERLAY_MARK = "→"


# ── data ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ScreenMessage:
    """A chat message recovered from the screen."""

    token: str   # addon-style event token, e.g. "GUILD" or "CHANNEL:2. Trade - City"
    author: str
    text: str

    def to_log_line(self) -> str | None:
        return make_synthetic_log_line(self.token, self.author, self.text)


@dataclass(frozen=True, slots=True)
class Entry:
    """One logical line of the chat frame: a message, or something else."""

    text: str                    # the joined text as read
    msg: ScreenMessage | None    # None for system lines, emotes, noise
    key: str                     # normalised, for comparing frames


# ── classification ───────────────────────────────────────────────────────────

_RE_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_RE_LEADING_CLOCK = re.compile(r"^\[?\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?\]?\s*")
_RE_CHANNEL_NUMBER = re.compile(r"^\d+\s*[.)]\s*")


def normalise_key(text: str) -> str:
    """A form of `text` that survives the small errors OCR makes between frames."""
    return _RE_NON_WORD.sub("", _RE_LEADING_CLOCK.sub("", text.strip()).lower())


def _strip_latin_marks(text: str) -> str:
    """Drop stray diacritics OCR adds to Latin letters ("Ġuild" -> "Guild").

    Only marks on ASCII letters go: Cyrillic "й" is a base letter plus a mark,
    and removing that mark would turn "Общий" into a different word.
    """
    out: list[str] = []
    for ch in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(ch) and out and out[-1].isascii() and out[-1].isalpha():
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def _closest(name: str, vocabulary) -> str | None:
    """`name` itself if known, else its nearest known spelling, else None."""
    if name in vocabulary:
        return name
    if len(name) < 4:
        return None
    match = get_close_matches(name, list(vocabulary), n=1, cutoff=0.8)
    return match[0] if match else None


def _channel_token(raw_channel: str) -> str:
    """Event token for the text between the brackets of a channel line.

    OCR misreads tags often enough that the name is matched loosely: stray marks
    are stripped and a near-miss snaps to the nearest known name. A numbered name
    ("2. Trade - Orgrimmar") is a public channel; only the built-in ones are named
    as such. Every other numbered channel is the player's own, and is sent with
    id 0 so the shared classifier treats it as Custom instead of guessing from a
    word that happens to appear in its name.
    """
    name = _strip_latin_marks(raw_channel.strip())
    numbered = bool(_RE_CHANNEL_NUMBER.match(name))
    bare = _RE_CHANNEL_NUMBER.sub("", name).strip()
    base = bare.split(" - ")[0].strip().lower()

    if not numbered:
        group = _closest(base, _GROUP_CHANNELS)
        if group:
            return _GROUP_CHANNELS[group]

    public = _closest(base, _PUBLIC_CHANNELS)
    if public:
        canonical, type_id = _PUBLIC_CHANNELS[public]
        return f"CHANNEL:{type_id}:{bare}" if type_id is not None else f"CHANNEL:{canonical}"
    return f"CHANNEL:0:{bare}"


def _who(match: re.Match[str]) -> str:
    """Author as the addon path writes it: "Name" or "Name-Realm"."""
    realm = match.groupdict().get("realm")
    return f"{match['author']}-{realm}" if realm else match["author"]


def classify_line(line: str) -> ScreenMessage | None:
    """Recognise a single logical line as a chat message, or return None."""
    line = line.strip()
    if not line or _OWN_OVERLAY_MARK in line:
        return None

    m = _RE_CHANNEL.match(line)
    if m:
        return ScreenMessage(_channel_token(m["chan"]), _who(m), m["text"].strip())

    m = _RE_WHISPER_FROM.match(line)
    if m:
        return ScreenMessage("WHISPER", _who(m), m["text"].strip())

    m = _RE_SAY_YELL.match(line)
    if m:
        return ScreenMessage(_SAY_VERBS[m["verb"].lower()], _who(m), m["text"].strip())

    m = _RE_WHISPER_TO.match(line) or _RE_WHISPER_TO_BRACKETED.match(line)
    if m:
        return ScreenMessage("WHISPER_INFORM", _who(m), m["text"].strip())

    return None


def _starts_new_entry(line: str) -> bool:
    return classify_line(line) is not None or bool(_RE_NEW_ENTRY_HINT.match(line.strip()))


# ── boxes -> lines -> entries ────────────────────────────────────────────────

def boxes_to_lines(boxes: list[Box], min_confidence: float = 0.35) -> list[str]:
    """Group recognised boxes into visual lines, top to bottom, left to right.

    A coloured channel prefix, the name and the message body often come back as
    separate boxes on one line, so boxes are joined by vertical overlap rather
    than assumed to be one per line.
    """
    usable = [b for b in boxes if b[5] >= min_confidence and b[4].strip()]
    if not usable:
        return []

    heights = sorted(b[3] - b[1] for b in usable)
    median_h = heights[len(heights) // 2] or 1.0
    usable.sort(key=lambda b: (b[1] + b[3]) / 2)

    rows: list[list[Box]] = []
    row_centre = 0.0
    for box in usable:
        centre = (box[1] + box[3]) / 2
        if rows and abs(centre - row_centre) <= median_h * 0.6:
            rows[-1].append(box)
            row_centre = sum((b[1] + b[3]) / 2 for b in rows[-1]) / len(rows[-1])
        else:
            rows.append([box])
            row_centre = centre

    return [
        " ".join(b[4].strip() for b in sorted(row, key=lambda b: b[0]))
        for row in rows
    ]


def frame_from_lines(lines: list[str]) -> list[Entry]:
    """Join wrapped visual lines into logical entries.

    The chat frame does not indent a wrapped line, so the only signal that a
    line continues the one above is that it does not begin a message of its own.
    """
    entries: list[tuple[list[str], bool]] = []  # (parts, previous_is_message)
    for raw in lines:
        line = raw.strip()
        if not line or _OWN_OVERLAY_MARK in line:
            continue
        if entries and entries[-1][1] and not _starts_new_entry(line):
            entries[-1][0].append(line)
            continue
        entries.append(([line], classify_line(line) is not None))

    frame: list[Entry] = []
    for parts, _is_msg in entries:
        text = " ".join(parts)
        message = classify_line(text)
        if message is None and not _RE_NEW_ENTRY_HINT.match(text):
            # The capture region is the in-game chat frame: text rendered there
            # is already visible to the player. Do not discard it just because
            # OCR could not recover the channel/author prefix. Preserve the full
            # line as a Custom-channel message so it still reaches translation.
            # Keep the existing exclusions for recognisable system notifications.
            message = ScreenMessage("CHANNEL:0:OCR", "Unknown", text)
        frame.append(Entry(text=text, msg=message, key=normalise_key(text)))
    return frame


# ── frame diffing ────────────────────────────────────────────────────────────

def _similar(a: str, b: str, threshold: float) -> bool:
    if a == b:
        return True
    if not a or not b:
        return False
    # Cheap length gate before the quadratic comparison.
    if min(len(a), len(b)) / max(len(a), len(b)) < threshold:
        return False
    return SequenceMatcher(None, a, b).ratio() >= threshold


class ChatDiffer:
    """Reports only the messages that appeared since the previous frame.

    Each capture shows the whole chat window again, scrolled by however many
    lines arrived. Which end the new lines enter from depends on the chat: the
    game's default is the bottom, with everything above shifting up, but a chat
    can be set to insert at the top and push the rest down. The differ does not
    ask which; it tries both alignments and locks on the one that finds the
    overlap, so a chat in either order works without a setting.

    Comparison is fuzzy because the same line is rarely read identically twice.
    """

    #: Frames in a row that match neither order before the direction is forgotten.
    _FORGET_AFTER = 3

    def __init__(self, match_threshold: float = 0.82, memory: int = 300) -> None:
        self._threshold = match_threshold
        self._prev: list[Entry] = []
        self._seen: deque[str] = deque(maxlen=memory)
        self._primed = False
        self._direction: str | None = None  # "bottom" | "top" once known
        self._misses = 0

    @property
    def primed(self) -> bool:
        """True once the first frame has been taken as the baseline."""
        return self._primed

    @property
    def direction(self) -> str | None:
        """Where new lines enter the chat, once it has been seen: "bottom" or "top"."""
        return self._direction

    def reset(self) -> None:
        self._prev = []
        self._seen.clear()
        self._primed = False
        self._direction = None
        self._misses = 0

    def baseline(self) -> list[ScreenMessage]:
        """The messages that were already on screen when the first frame was taken.

        They are not reported as new (translating the scrollback the user had
        before starting would be noise), but the caller may want to show them.
        """
        return [e.msg for e in self._prev if e.msg is not None] if self._primed else []

    def update(self, frame: list[Entry]) -> list[ScreenMessage]:
        if not self._primed:
            self._prime(frame)
            return []
        if not frame:
            return []

        fresh = self._new_entries(frame)
        self._prev = frame
        out: list[ScreenMessage] = []
        for entry in fresh:
            self._seen.append(entry.key)
            if entry.msg is not None:
                out.append(entry.msg)
        return out

    # internals -------------------------------------------------------------

    def _prime(self, frame: list[Entry]) -> None:
        self._prev = frame
        for entry in frame:
            self._seen.append(entry.key)
        self._primed = True

    def _was_seen(self, key: str) -> bool:
        return any(_similar(key, old, 0.9) for old in self._seen)

    def _new_entries(self, frame: list[Entry]) -> list[Entry]:
        """The entries of `frame` that are new, oldest first."""
        counts: dict[str, int] = {}
        for direction in ((self._direction,) if self._direction else ("bottom", "top")):
            found = self._new_count(frame, direction)
            if found is not None:
                counts[direction] = found

        if counts:
            self._misses = 0
            direction = self._choose(counts)
            # Lock only on evidence: this order explains new lines and the other
            # either found no overlap at all or found nothing new.
            others = [c for d, c in counts.items() if d != direction]
            if self._direction is None and counts[direction] > 0 and all(c == 0 for c in others):
                self._direction = direction
            return self._take(frame, direction, counts[direction])

        # Neither order lines up with the last frame: a burst longer than the
        # window, a cleared chat, or a misread frame. Filter what was already
        # delivered, so a bad read cannot replay old lines.
        self._misses += 1
        if self._misses >= self._FORGET_AFTER:
            self._direction = None
        fresh = [e for e in frame if not self._was_seen(e.key)]
        return fresh[::-1] if self._direction == "top" else fresh

    @staticmethod
    def _choose(counts: dict[str, int]) -> str:
        if len(counts) == 1:
            return next(iter(counts))
        # Both orders found an overlap. With a window that is not full yet, the
        # wrong order can "match" trivially by finding nothing new, so a result
        # that does report new lines beats one that reports none; otherwise the
        # smaller number of new lines wins, and the game's own order breaks a tie.
        bottom, top = counts["bottom"], counts["top"]
        if bottom == top:
            return "bottom"
        if bottom == 0:
            return "top"
        if top == 0:
            return "bottom"
        return "bottom" if bottom < top else "top"

    @staticmethod
    def _take(frame: list[Entry], direction: str, count: int) -> list[Entry]:
        if count <= 0:
            return []
        if direction == "bottom":
            return frame[len(frame) - count:]
        return frame[:count][::-1]  # newest is first on screen; deliver oldest first

    def _new_count(self, frame: list[Entry], direction: str) -> int | None:
        """How many entries are new if new lines enter from `direction`, else None."""
        prev = self._prev
        if not prev:
            return len(frame)
        if direction == "bottom":
            # The previous tail is the new head, shifted by however many arrived.
            for shift in range(0, len(prev)):
                overlap = len(prev) - shift
                if overlap > len(frame):
                    continue
                if self._lines_match(prev[shift:], frame[:overlap]):
                    return len(frame) - overlap
            return None
        # "top": the previous head is the new tail, pushed down by the new lines.
        for pushed in range(0, len(frame)):
            overlap = min(len(prev), len(frame) - pushed)
            if overlap <= 0:
                break
            if self._lines_match(prev[:overlap], frame[pushed:pushed + overlap]):
                return pushed
        return None

    def _lines_match(self, old: list[Entry], new: list[Entry]) -> bool:
        matched = sum(1 for a, b in zip(old, new, strict=True) if _similar(a.key, b.key, self._threshold))
        # One misread line in an otherwise matching overlap must not break the
        # alignment, but a one- or two-line overlap has to match outright.
        needed = len(old) if len(old) <= 2 else len(old) - 1
        return matched >= needed
