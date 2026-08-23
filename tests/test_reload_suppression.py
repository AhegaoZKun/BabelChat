"""What a /reload suppresses, and for how long.

Reloading the interface restarts the addon's sequence numbers, so the reader
sees a buffer whose numbers are lower than the ones it has already delivered.
It handles that by rewinding and, for a minute afterwards, skipping any message
whose text it had already shown — otherwise the whole ring is delivered twice.

The list has to stop suppressing when the minute is up. It was cleared at the
END of the method that filters against it, so the first message to arrive after
the window was still matched against the stale list and dropped. In guild chat
the texts on that list are "привет" and "ку".
"""

from __future__ import annotations

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("the Windows memory reader", allow_module_level=True)

pytest.importorskip("pymem", reason="the Windows memory reader")

import app.memory_reader_windows as module  # noqa: E402


@pytest.fixture
def reader():
    delivered: list[str] = []
    r = module.WoWAddonBufReader(lambda line, **_: delivered.append(line))
    r.delivered = delivered  # type: ignore[attr-defined]
    return r


def test_a_repeat_right_after_a_reload_is_suppressed():
    """The point of the list: the addon rebuilt its ring from zero and is
    offering the same messages again."""
    delivered: list[str] = []
    reader = module.WoWAddonBufReader(lambda line, **_: delivered.append(line))

    reader._deliver_new_messages("5|RAW|SAY|Player|привет")
    assert len(delivered) == 1

    # A reload: sequence numbers restart below where we were.
    reader._deliver_new_messages("1|RAW|SAY|Player|привет")

    assert len(delivered) == 1, "the same message was delivered twice after a reload"


def test_the_same_words_are_delivered_again_once_the_window_is_up(monkeypatch):
    """Someone says "привет" a minute later and it is a different greeting.

    This is the one the ordering cost: the list was cleared after the filtering
    loop, so the first message past the deadline was still measured against it.
    """
    delivered: list[str] = []
    reader = module.WoWAddonBufReader(lambda line, **_: delivered.append(line))

    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])

    reader._deliver_new_messages("5|RAW|SAY|Player|привет")
    reader._deliver_new_messages("1|RAW|SAY|Player|привет")
    assert len(delivered) == 1

    clock["now"] += 61.0
    reader._deliver_new_messages("2|RAW|SAY|Player|привет")

    assert len(delivered) == 2, "a genuine repeat a minute later is still being suppressed"


def test_the_window_does_not_suppress_anything_else(monkeypatch):
    """Only texts seen before the reload. A message nobody has said before must
    go through whatever else is happening."""
    delivered: list[str] = []
    reader = module.WoWAddonBufReader(lambda line, **_: delivered.append(line))

    reader._deliver_new_messages("5|RAW|SAY|Player|привет")
    reader._deliver_new_messages("1|RAW|SAY|Player|совершенно новая фраза")

    assert len(delivered) == 2


def test_the_expiry_is_checked_before_anything_is_filtered():
    """Behaviour covers this, and the source says it plainly enough to keep
    someone from moving it back."""
    import pathlib

    for name in ("memory_reader_windows.py", "memory_reader_linux.py"):
        body = (pathlib.Path(__file__).resolve().parent.parent / "app" / name).read_text(encoding="utf-8")
        method = body[body.index("def _deliver_new_messages") :]
        method = method[: method.index("\n    def ", 10)]

        cleared = method.index("self._pre_reset_texts.clear()")
        filtered = method.index("in self._pre_reset_texts")

        assert cleared < filtered, f"{name} clears the list after filtering against it"
