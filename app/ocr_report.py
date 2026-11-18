"""Turn one read of the chat area into something a person can act on.

The screen source has a failure mode the memory source does not: it works, text
comes back, and nothing reaches the overlay. There are only a few reasons, and
each one looks different in the text that was read, so the test result shows the
reader's own view of it line by line:

    ✓  a chat message: it will appear in the overlay
    ?  text that is not a chat message (a system line, or a format not recognised)

That turns "it does not work" into something the user can read off the screen.
"""

from __future__ import annotations

from app.i18n import tr
from app.ocr_reader import ReadReport
from app.parser import parse_line

_MAX_ROWS = 8
_MAX_TEXT = 70


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 1] + "…"


def format_report(report: ReadReport) -> str:
    """The read, row by row."""
    if not report.lines:
        return tr("wizard.source.test_empty")

    chat = [e for e in report.entries if e.msg is not None]
    rows = [tr("wizard.source.test_header", lines=len(report.lines), chat=len(chat))]

    if not chat:
        rows.append(tr("wizard.source.test_none_chat"))

    for entry in report.entries[-_MAX_ROWS:]:
        if entry.msg is None:
            rows.append(tr("wizard.source.test_row_unknown", text=_clip(entry.text)))
            continue
        parsed = parse_line(entry.msg.to_log_line() or "")
        channel = parsed.channel if parsed else None
        label = channel.value if channel else entry.msg.token
        fields = {"channel": label, "author": entry.msg.author, "text": _clip(entry.msg.text)}
        rows.append(tr("wizard.source.test_row_ok", **fields))
    return "\n".join(rows)
