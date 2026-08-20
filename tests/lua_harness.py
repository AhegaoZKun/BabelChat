"""Run the WoW addon's Lua under a real Lua 5.1 interpreter.

WoW runs Lua 5.1, and `lupa.lua51` embeds exactly that, so addon logic can be
exercised for real instead of being eyeballed or re-implemented in Python. What
the harness supplies is the surrounding game: the handful of WoW globals the
addon touches, plus a manual clock and a manual ticker so tests drive time
rather than wait for it.

Fidelity, stated plainly so nobody over-trusts a green test:

* `SecretValue` models a secret chat argument by the one property the addon
  actually relies on — `string.len` raises on it while concatenation succeeds.
  A real secret also raises on comparison and on boolean tests; plain Lua has
  no way to make `==` raise, so a guard that compares a secret would pass here
  and fail in game. Read secret tests as "the probe works", not "no unguarded
  operation exists anywhere".
* Only the globals listed in `_install_wow_globals` exist. Anything else the
  addon reaches for surfaces as a nil-index error, which is the intended
  outcome: the harness should fail loudly rather than paper over a new
  dependency on game state.
"""

from __future__ import annotations

from pathlib import Path

from lupa import lua51

ADDON_DIR = Path(__file__).resolve().parent.parent / "addon" / "BabelChat"

# Lua source for a stand-in secret value. `string.len` raises on it (it is not a
# string), while `..` succeeds through __concat — the same asymmetry the real
# chat-messaging-lockdown secrets have, and the asymmetry the addon's probe
# depends on.
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

    def buffer_entries(self) -> list[str]:
        """Buffer payload lines: everything between the header and the end marker."""
        text = self.buffer_text()
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
