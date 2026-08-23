# Frontend architecture: Linux (GTK) vs Windows (PyQt)

BabelChat has **two** UI frontends sharing one engine. This is deliberate.

## Why two frontends

The engine (memory scanner, parser, detector, cache, phrasebook, translation
providers, pipeline) is UI-agnostic and shared by both. Only the UI differs:

- **Linux → GTK4** (`main_gtk.py`, `overlay_gtk.py`, `settings_gtk.py`,
  `setup_wizard_gtk.py`, `tray_sni.py`, `x11_window.py`, `hotkeys_linux.py`).
  On Wayland only a layer-shell surface can sit above a true-fullscreen game;
  a normal always-on-top window cannot.
- **Windows → PyQt6** (`main.py`, `overlay.py`, `overlay_chrome.py`,
  `overlay_reply.py`, `overlay_widgets.py`, `settings_dialog.py`,
  `tray.py`, `about_dialog.py`, `about_tab_qt.py`, `setup_wizard.py`,
  `wizard_pages_qt.py`, `wizard_style.py`, `provider_settings_qt.py`,
  `qt_theme.py`, `qt_widgets.py`, `hotkey_edit.py`, `hotkeys_windows.py`).
  Windows has no layer-shell concept; the PyQt always-on-top overlay works fine
  there.

`hotkeys.py` and `memory_reader.py` are platform dispatchers: each picks the
Windows or Linux implementation from `sys.platform`, so callers import one name.

## The Linux overlay has three modes, not two

`ChatOverlayGtk._detect_mode()` decides at startup, from the GDK backend:

| Session | Mode | What the window is |
|---------|------|--------------------|
| Wayland, compositor advertises layer-shell | `layer` | A gtk4-layer-shell surface above fullscreen WoW |
| X11 or XWayland | `x11` | A normal window with EWMH `_NET_WM_STATE_ABOVE`, positioned through `x11_window.py` |
| Anything else — notably GNOME Wayland, where Mutter refuses the layer-shell protocol | `plain` | An ordinary window, with a one-time dialog saying so |

The layer-shell library is loaded by `dlopen` before `gi` pulls in
libwayland-client, and its typelib import is guarded. A machine without
gtk4-layer-shell installed logs a warning and drops to `x11` or `plain`; it does
not fail to start. That fallback is the whole reason the guard exists — an
import-time crash there shipped once.

## IMPORTANT: do not delete the PyQt files

The PyQt overlay stack is **not dead code** — it is the Windows UI. It was only
*deprecated on Linux*, where GTK replaces it. Deleting `overlay.py` et al. would
break the Windows build. They stay.

What was actually removed/deprecated on Linux:
- Linux no longer launches the PyQt overlay or forces `QT_QPA_PLATFORM=xcb`.
- The Linux entry point is `main_gtk.py`, not `main.py`.

## Entry points / builds

| Platform | Entry          | Build spec         | Overlay tech            |
|----------|----------------|--------------------|-------------------------|
| Linux    | `app/main_gtk.py` | `build-linux.spec` | GTK4 + gtk4-layer-shell |
| Windows  | `app/main.py`     | `build.spec`       | PyQt6                   |

## Shared (used by both, never delete)

`config`, `parser`, `pipeline`, `dedup`, `detector`, `cache`, `phrasebook`,
`glossary`, `glossary_data`, `slang`, `text_utils`, `languages`, `watcher`,
`i18n`, `debug_log`, `addon_protocol`, `native_scanner`,
`memory_reader` (+ `_linux` / `_windows`), `translator` and the
`app/translators/` package.

## Not shared, and easy to mistake for shared

`overlay_theme.py` is read by the GTK frontend only: `overlay_gtk` and
`settings_gtk` render the `overlay_*` colour fields from the config through it,
while the PyQt overlay takes its colours from the hard-coded `CHANNEL_COLORS`
and `TRANSLATION_COLOR` in `overlay_widgets.py`. Changing a theme preset
therefore changes the Linux overlay and nothing on Windows.
