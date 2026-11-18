"""The screen source never opens the game process.

That is the whole point of choosing it, so it is asserted over the source rather
than promised in a description (issue #17 sets the same bar for the pixel
transport). Two checks: nothing in the screen-capture modules names an API that
opens another process or reads its memory, and the pipeline, set to the screen
source, never constructs the memory reader.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from lingua import Language

from app.pipeline import PipelineConfig, TranslationPipeline

APP = Path(__file__).resolve().parent.parent / "app"

# Every module that is part of reading chat off the screen.
SCREEN_MODULES = (
    "ocr_parse.py",
    "ocr_engine.py",
    "ocr_capture.py",
    "ocr_reader.py",
    "ocr_report.py",
    "capture_source_gtk.py",
    "capture_source_qt.py",
    "region_picker_gtk.py",
    "region_picker_qt.py",
)

# What opening or reading another process looks like, on either platform.
FORBIDDEN = (
    r"ReadProcessMemory",
    r"OpenProcess",
    r"VirtualQueryEx",
    r"process_vm_readv",
    r"\bptrace\b",
    r"/proc/",
    r"\bpymem\b",
    r"\bpsutil\b",
    r"windll",
    r"babelchat_scanner",
    r"\bmemory_reader",
    r"WOW_PROCESS_NAMES",
    r"Wow\.exe",
)


def _code_without_comments_and_docstrings(path: Path) -> str:
    """Source with comments and docstrings removed: they may *mention* the forbidden."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            first = body[0] if body else None
            is_docstring = (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            )
            if is_docstring:
                first.value.value = ""
    return ast.unparse(tree)


@pytest.mark.parametrize("module", SCREEN_MODULES)
def test_a_screen_module_never_names_an_api_that_opens_the_game(module: str) -> None:
    code = _code_without_comments_and_docstrings(APP / module)
    hits = [pattern for pattern in FORBIDDEN if re.search(pattern, code)]
    assert hits == [], f"{module} refers to {hits}"


def test_every_screen_module_exists() -> None:
    """The list above must not silently stop checking a renamed file."""
    missing = [m for m in SCREEN_MODULES if not (APP / m).is_file()]
    assert missing == []


def test_the_screen_source_never_constructs_the_memory_reader(tmp_path) -> None:
    config = PipelineConfig(
        chatlog_path=tmp_path / "WoWChatLog.txt",
        providers={"deepl": {"api_key": "fake"}},
        target_lang="RU",
        own_language=Language.RUSSIAN,
        db_path=str(tmp_path / "cache.db"),
        use_memory_reader=True,  # even when the memory reader is allowed
        capture_source="ocr",
    )
    with (
        patch("app.pipeline.HAS_MEMORY_READER", True),
        patch("app.pipeline.MemoryChatWatcher", MagicMock(name="MemoryChatWatcher")) as memory_reader,
        patch("app.ocr_reader.OcrChatWatcher.start"),
    ):
        pipeline = TranslationPipeline(config, lambda _m: None)
        pipeline.start()
        pipeline.stop()
    memory_reader.assert_not_called()
