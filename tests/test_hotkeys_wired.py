"""A hotkey you can configure has to do something.

`hotkey_clipboard_translate` was offered in the settings window, saved to the
config and described with a hint — and registered by nobody. A user could pick a
combination, press it, and get nothing, with no error to report.
`hotkey_toggle_interactive` was worse: a config field with no UI and no reader.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "app" / "main.py"


def configurable_hotkeys() -> set[str]:
    """The hotkey fields AppConfig offers."""
    from app.config import AppConfig

    return {name for name in vars(AppConfig()) if name.startswith("hotkey_")}


def test_every_configurable_hotkey_is_registered():
    """Registration is what makes a key combination live. Anything the config
    offers and main.py never registers is a control that does nothing."""
    source = MAIN.read_text(encoding="utf-8")
    registered = set(re.findall(r"hotkey_mgr\.register\(config\.(\w+)\)", source))

    assert configurable_hotkeys() - registered == set(), (
        f"offered but never registered: {sorted(configurable_hotkeys() - registered)}"
    )


def test_every_registered_hotkey_has_an_action():
    """Registering without a handler is the same dead control one layer down."""
    source = MAIN.read_text(encoding="utf-8")
    block = source[source.index("hotkey_mgr = GlobalHotkeyManager()") :]
    block = block[: block.index("hotkey_mgr.start()")]

    registrations = re.findall(r"hotkey_mgr\.register\(config\.\w+\):\s*([\w.]+)", block)

    assert len(registrations) == len(configurable_hotkeys()), block
    assert all(action for action in registrations), registrations


def test_every_hotkey_the_settings_window_shows_is_a_real_setting():
    """A row in the window for a field that no longer exists renders as a blank
    control bound to nothing."""
    from app.config import AppConfig

    dialog = (ROOT / "app" / "settings_dialog.py").read_text(encoding="utf-8")
    shown = set(re.findall(r"self\._config\.(hotkey_\w+)", dialog))

    unknown = {name for name in shown if not hasattr(AppConfig(), name)}
    assert unknown == set(), f"the settings window binds to fields that do not exist: {unknown}"


def test_the_clipboard_action_exists_on_the_overlay():
    """main.py points the hotkey at it by name; a rename would leave the key
    silently dead again."""
    from app.overlay import ChatOverlay

    assert callable(ChatOverlay.translate_clipboard)


def test_no_orphaned_hotkey_strings_remain():
    """`tr` renders a missing key as its own name, but an unused key is the
    other half of the same problem: copy for a control nobody can reach."""
    from app.i18n import _STRINGS

    hotkey_keys = {key for key in _STRINGS if key.startswith("settings.hk.")}
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "app").glob("*.py")
    )

    unused = {key for key in hotkey_keys if f'"{key}"' not in source}
    assert unused == set(), f"strings for hotkeys that no longer exist: {unused}"


@pytest.mark.parametrize("language", ["RU", "EN", "ES"])
def test_the_clipboard_result_is_reported_in_every_language(language):
    from app.i18n import _STRINGS

    for key in ("overlay.clipboard.empty", "overlay.clipboard.done"):
        assert language in _STRINGS[key], f"{key} has no {language}"
