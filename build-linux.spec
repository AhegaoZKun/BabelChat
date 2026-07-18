# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for BabelChat single binary (Linux, GTK4 + layer-shell).

The Linux frontend uses GTK4 + gtk4-layer-shell (see ARCHITECTURE_FRONTENDS.md).
Bundling GTK via PyInstaller relies on PyGObject's bundled hooks to collect the
GObject-Introspection typelibs and the GTK shared libraries; on top of that we
must explicitly collect the gtk4-layer-shell library + its typelib, which the
generic gi hook does not know about.

Build:  pyinstaller build-linux.spec
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules
import glob
import os

# --- Locate the gtk4-layer-shell native lib + typelib so they get bundled ----
def _find(paths):
    for p in paths:
        for hit in glob.glob(p):
            return hit
    return None

_layer_so = _find([
    "/usr/lib/libgtk4-layer-shell.so*",
    "/usr/lib/*/libgtk4-layer-shell.so*",
    "/usr/lib64/libgtk4-layer-shell.so*",
])
_layer_typelib = _find([
    "/usr/lib/girepository-1.0/Gtk4LayerShell-1.0.typelib",
    "/usr/lib/*/girepository-1.0/Gtk4LayerShell-1.0.typelib",
    "/usr/lib64/girepository-1.0/Gtk4LayerShell-1.0.typelib",
])

_extra_binaries = []
if _layer_so:
    _extra_binaries.append((_layer_so, "."))

_extra_datas = []
if _layer_typelib:
    # PyGObject's loader looks for typelibs under gi_typelibs/ in the bundle.
    _extra_datas.append((_layer_typelib, "gi_typelibs"))

# PyGObject + GTK collection (typelibs, libs, data) via the gi hooks.
gi_datas, gi_binaries, gi_hidden = collect_all("gi")[:3]

a = Analysis(
    ["app/main_gtk.py"],
    pathex=["."],
    binaries=[
        ("app/libbabelchat_scanner.so", "."),
        *_extra_binaries,
        *gi_binaries,
    ],
    datas=[
        ("assets/icon.png", "assets"),
        ("addon/BabelChat", "addon/BabelChat"),
        *_extra_datas,
        *gi_datas,
    ],
    hiddenimports=[
        # Engine / non-UI
        "lingua",
        "lingua.builder",
        "deepl",
        "watchdog",
        "watchdog.observers",
        "watchdog.events",
        "dotenv",
        "app.watcher",
        "app.parser",
        "app.detector",
        "app.translator",
        "app.cache",
        "app.dedup",
        "app.pipeline",
        "app.config",
        "app.text_utils",
        "app.memory_reader",
        "app.memory_reader_linux",
        "app.i18n",
        "app.phrasebook",
        "app.glossary",
        "app.glossary_data",
        "app.slang",
        # GTK frontend
        "app.main_gtk",
        "app.overlay_gtk",
        "app.settings_gtk",
        # PyGObject / GTK
        "gi",
        "gi.repository.Gtk",
        "gi.repository.Gdk",
        "gi.repository.GLib",
        "gi.repository.GObject",
        "gi.repository.Gio",
        "gi.repository.Pango",
        "gi.repository.Gtk4LayerShell",
        *gi_hidden,
        *collect_submodules("gi"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "PIL",
        "pymem",
        "pymem.process",
        "pymem.exception",
        # Windows-only
        "app.memory_reader_windows",
        "app.hotkeys_windows",
        # PyQt frontend is Windows-only; exclude it from the Linux GTK build.
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "app.main",
        "app.overlay",
        "app.tray",
        "app.settings_dialog",
        "app.setup_wizard",
        "app.about_dialog",
        "app.hotkeys",
        "app.hotkeys_linux",
        "pynput",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
# onedir build: EXE holds only the bootloader/scripts; binaries+datas are
# gathered by COLLECT into dist/BabelChat/. This (not onefile) is what
# linuxdeploy needs so its GTK plugin can bundle the full GTK stack alongside
# the exposed executable for a self-contained "fat" AppImage.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BabelChat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon="assets/icon.png",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BabelChat",
)
