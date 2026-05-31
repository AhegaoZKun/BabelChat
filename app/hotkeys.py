"""Platform dispatcher for global hotkeys.

Selects the correct implementation based on the current OS.
"""

import sys

if sys.platform == "win32":
    from app.hotkeys_windows import GlobalHotkeyManager, parse_hotkey  # noqa: F401
else:
    from app.hotkeys_linux import GlobalHotkeyManager, parse_hotkey  # noqa: F401
