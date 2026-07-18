# Frontend architecture: Linux (GTK) vs Windows (PyQt)

BabelChat has **two** UI frontends sharing one engine. This is deliberate.

## Why two frontends

The engine (memory scanner, parser, detector, cache, phrasebook, translator,
pipeline) is UI-agnostic and shared by both. Only the UI differs:

- **Linux / Wayland → GTK4 + gtk4-layer-shell** (`main_gtk.py`, `overlay_gtk.py`,
  `settings_gtk.py`). Required because only a layer-shell surface can sit above a
  true-fullscreen game on Wayland/KWin. A normal always-on-top window cannot.
- **Windows → PyQt6** (`main.py`, `overlay.py`, `settings_dialog.py`, `tray.py`,
  `reply_widget.py`, `about_dialog.py`, `setup_wizard.py`, `lang_selector.py`,
  `hotkeys*`). Windows has no layer-shell concept; the PyQt always-on-top overlay
  works fine there.

## IMPORTANT: do not delete the PyQt files

The PyQt overlay stack is **not dead code** — it is the Windows UI. It was only
*deprecated on Linux*, where GTK replaces it. Deleting `overlay.py` et al. would
break the Windows build. They stay.

What was actually removed/deprecated on Linux:
- Linux no longer launches the PyQt overlay or forces `QT_QPA_PLATFORM=xcb`.
- The Linux entry point is now `main_gtk.py`, not `main.py`.

## Entry points / builds

| Platform | Entry          | Build spec         | Overlay tech            |
|----------|----------------|--------------------|-------------------------|
| Linux    | `app/main_gtk.py` | `build-linux.spec` | GTK4 + gtk4-layer-shell |
| Windows  | `app/main.py`     | `build.spec`       | PyQt6                   |

## Shared (used by both, never delete)

config, parser, pipeline, translator, detector, cache, dedup, phrasebook,
glossary, glossary_data, slang, text_utils, watcher, i18n,
memory_reader(+_linux/_windows).
