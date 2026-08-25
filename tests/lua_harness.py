"""Run the WoW addon's Lua under a real Lua 5.1 interpreter.

WoW runs Lua 5.1, and `lupa.lua51` embeds exactly that, so addon logic can be
exercised for real instead of being eyeballed or re-implemented in Python. What
the harness supplies is the surrounding game: the handful of WoW globals the
addon touches, plus a manual clock and a manual ticker so tests drive time
rather than wait for it.

Fidelity, stated plainly so nobody over-trusts a green test:

* The secret stand-in is a **table**, and a real secret reports as a string.
  Plain Lua cannot produce a value that answers `type()` with "string" and then
  raises on `string.len`, so the stand-in cannot model a secret's defining
  property. What the secret tests actually exercise is the addon's `type` check
  — the same branch a number or a boolean takes. The `pcall(string.len, …)`
  probe behind it is not covered here and cannot be; it is verified in game.
  Do not read a green secret test as "a secret cannot reach the buffer".
* Only the globals listed in `_install_wow_globals` exist. Anything else the
  addon reaches for surfaces as a nil-index error, which is the intended
  outcome: the harness should fail loudly rather than paper over a new
  dependency on game state. Files that need more than that — `LibStub`,
  `CreateFrame` — stub it themselves, per test file.
* `NUM_CHAT_WINDOWS` is 0 and `LoggingChat` is always false, so the legacy
  chat-frame polling path never runs here. `C_Timer.NewTicker` ignores its
  interval: `fire_tickers()` runs every live ticker once, so a test proves that
  a ticker's body works, never that it fires at the right rate.
* The process C locale is pinned to "C" at import (see below). Without it
  `string.lower` and `%w`/`%s` behave differently from WoW for every byte above
  127, which silently changes what a Cyrillic test proves.
"""

from __future__ import annotations

import locale
from pathlib import Path

from lupa import lua51

# Lua's string.lower and its %w/%s classes call the C library's tolower and
# isalpha, which read the process locale. Something in the test environment —
# PyQt6, or lupa itself — sets LC_CTYPE from Windows, and under
# Russian_Russia.1252 the results are wrong in ways that matter here: two
# distinct Cyrillic letters lower-case to the same bytes, and byte 0xA0, the
# trail byte of "Р", matches %s so the tokeniser splits a word mid-character.
# WoW runs under the C locale. Pin it, or every Cyrillic assertion in the suite
# is measuring this machine rather than the game.
locale.setlocale(locale.LC_CTYPE, "C")

ADDON_DIR = Path(__file__).resolve().parent.parent / "addon" / "BabelChat"

# Stand-in for a chat-messaging-lockdown secret. It is a table, so it exercises
# the addon's `type` check and nothing beyond it — see the fidelity notes above.
_SECRET_FACTORY = """
return function()
    return setmetatable({}, {
        __concat = function(a, b) return "<secret>" end,
        __tostring = function() return "<secret>" end,
    })
end
"""


class AddonHarness:
    """A Lua 5.1 runtime preloaded with WoW stubs and the addon's files."""

    def __init__(self) -> None:
        self.lua = lua51.LuaRuntime(unpack_returned_tuples=True)
        self.now = 0.0
        self.tickers: list[dict] = []
        self._install_wow_globals()
        self._make_secret = self.lua.execute(_SECRET_FACTORY)
        self.addon_table = self.lua.eval("{}")
        self.lua.globals().BabelChatDB = self.lua.eval("{}")

    # ── game stubs ───────────────────────────────────────────────────────

    def _install_wow_globals(self) -> None:
        g = self.lua.globals()

        g.GetTime = lambda: self.now
        g.UnitName = lambda _unit: "Tester"
        g.GetNormalizedRealmName = lambda: "TestRealm"
        g.LoggingChat = lambda *_a: False
        g.NUM_CHAT_WINDOWS = 0

        # WoW exposes these as globals; Lua 5.1 only has table.insert/remove.
        self.lua.execute("tinsert = table.insert; tremove = table.remove")

        def new_ticker(interval, callback):
            entry = {"interval": interval, "callback": callback, "cancelled": False}
            self.tickers.append(entry)
            return self.lua.table_from({"Cancel": lambda _self: entry.__setitem__("cancelled", True)})

        g.C_Timer = self.lua.table_from({"NewTicker": new_ticker})

    # ── driving the world ────────────────────────────────────────────────

    def advance(self, seconds: float) -> None:
        """Move the clock. Does not fire tickers — call `fire_tickers` for that."""
        self.now += seconds

    def fire_tickers(self) -> None:
        """Run every live ticker callback once, in registration order."""
        for entry in list(self.tickers):
            if not entry["cancelled"]:
                entry["callback"]()

    def secret(self):
        """A stand-in for a secret chat argument. See the module docstring."""
        return self._make_secret()

    # ── loading addon files ──────────────────────────────────────────────

    def load(self, *filenames: str) -> None:
        """Load addon files in order, passing the usual (name, addonTable) varargs."""
        for name in filenames:
            source = (ADDON_DIR / name).read_text(encoding="utf-8")
            chunk = self.lua.execute("return function(src, chunkname) return assert(loadstring(src, chunkname)) end")(
                source, "@" + name
            )
            chunk("BabelChat", self.addon_table)

    # ── convenience accessors ────────────────────────────────────────────

    @property
    def db(self):
        return self.lua.globals().BabelChatDB

    def enable_companion(self) -> None:
        self.lua.execute("BabelChatDB.companion = { enabled = true, flushInterval = 5 }")

    def buffer_text(self) -> str:
        raw = self.db.wctbuf
        return raw if raw is not None else ""

    def buffer_frame(self) -> str:
        """The buffer up to and including the end marker, without the padding.

        Every real reader stops at the marker; the buffer is padded past it to a
        fixed length so that rebuilding it does not move it in memory.
        """
        text = self.buffer_text()
        marker = "__WCT_END__"
        cut = text.find(marker)
        return text if cut == -1 else text[: cut + len(marker)]

    def buffer_entries(self) -> list[str]:
        """Buffer payload lines: everything between the header and the end marker."""
        text = self.buffer_frame()
        if not text:
            return []
        lines = text.split("\n")
        # First line is the __WCT_BUF_NNNN__ header, last is __WCT_END__.
        body = lines[1:-1]
        return [ln for ln in body if ln and not ln.startswith("0|META|")]


def load_companion_buffer() -> AddonHarness:
    """Harness with CompanionBuffer.lua loaded and the companion switched on."""
    harness = AddonHarness()
    harness.load("CompanionBuffer.lua")
    harness.enable_companion()
    harness.addon_table.PreallocateCompanionKeys()
    return harness
