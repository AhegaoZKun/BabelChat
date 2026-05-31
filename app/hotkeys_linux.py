"""Global hotkey support — Linux implementation using pynput.

pynput works on X11 and Wayland (via XWayland for key capture).
Note: on pure Wayland compositors without XWayland, global hotkeys
may not function — pynput will still load but key events outside
the app window may not be captured. This is a Wayland limitation,
not specific to BabelChat.
"""

from __future__ import annotations

import logging
import threading

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


def parse_hotkey(hotkey_str: str) -> str:
    """Convert 'Ctrl+Shift+T' to pynput combination string '<ctrl>+<shift>+t'.

    Returns empty string if invalid.
    """
    parts = [p.strip() for p in hotkey_str.split("+")]
    result = []
    for part in parts:
        upper = part.upper()
        if upper in ("CTRL", "CONTROL"):
            result.append("<ctrl>")
        elif upper == "SHIFT":
            result.append("<shift>")
        elif upper == "ALT":
            result.append("<alt>")
        elif len(part) == 1 and part.isalpha():
            result.append(part.lower())
        elif upper.startswith("F") and upper[1:].isdigit():
            result.append(f"<{part.lower()}>")
        else:
            logger.warning("Unknown key: %s", part)
            return ""
    return "+".join(result)


class GlobalHotkeyManager(QObject):
    """Manages global hotkeys using pynput.

    Runs a listener in a background thread.
    Emits Qt signals when hotkeys are pressed.

    On Wayland without XWayland, global hotkeys may not work outside
    the app window. BabelChat will still function normally; only the
    hotkey toggle feature will be unavailable.
    """

    hotkey_pressed = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self._hotkeys: dict[int, str] = {}  # id -> pynput combo string
        self._next_id = 1
        self._listener = None
        self._running = False

    def register(self, hotkey_str: str) -> int:
        """Register a global hotkey. Returns hotkey_id (0 on failure)."""
        combo = parse_hotkey(hotkey_str)
        if not combo:
            return 0
        hotkey_id = self._next_id
        self._next_id += 1
        self._hotkeys[hotkey_id] = combo
        return hotkey_id

    def start(self) -> None:
        """Start listening for global hotkeys."""
        try:
            from pynput import keyboard

            # Build callback map: pynput combo → hotkey_id
            combo_to_id = {combo: hk_id for hk_id, combo in self._hotkeys.items()}

            def on_activate(combo_str: str) -> None:
                hk_id = combo_to_id.get(combo_str)
                if hk_id is not None:
                    self.hotkey_pressed.emit(hk_id)

            hotkey_map = {
                combo: (lambda c=combo: on_activate(c))
                for combo in combo_to_id
            }

            self._listener = keyboard.GlobalHotKeys(hotkey_map)
            self._listener.start()
            self._running = True
            logger.info("Global hotkeys registered via pynput: %s", list(combo_to_id.keys()))
        except ImportError:
            logger.warning(
                "pynput not installed — global hotkeys unavailable. "
                "Install with: pip install pynput"
            )
        except Exception as e:
            logger.warning(
                "Failed to start global hotkeys (Wayland without XWayland?): %s", e
            )

    def stop(self) -> None:
        """Stop listening."""
        self._running = False
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
