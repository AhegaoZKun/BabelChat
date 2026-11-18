# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for BabelChat single .exe build."""

from PyInstaller.utils.hooks import collect_all

# The screen (OCR) chat source. RapidOCR ships its model files and YAML config as
# package data, and onnxruntime its native library; neither is found by import
# analysis, so without these the default chat source cannot start in the .exe.
_ocr_datas, _ocr_binaries, _ocr_hidden = [], [], []
for _pkg in ("rapidocr", "onnxruntime"):
    _d, _b, _h = collect_all(_pkg)
    _ocr_datas += _d
    _ocr_binaries += _b
    _ocr_hidden += _h

# Per-script recognition models fetched by tools/fetch_ocr_models.py, if the
# build machine ran it. The default (Chinese/English) model already rides in the
# rapidocr wheel, so only the other scripts are listed.
import glob as _glob
_ocr_models = [(_p, "ocr_models") for _p in _glob.glob("ocr_models/*_PP-OCR*_rec_*.onnx")]

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=[('app/babelchat_scanner_win.dll', '.'), *_ocr_binaries],
    datas=[
        *_ocr_datas,
        *_ocr_models,
        ("assets/icon.ico", "assets"),
        ("assets/icon.png", "assets"),
        # GigaChat is served behind a root certifi does not carry, so
        # without this the default provider cannot connect at all.
        ("assets/certs", "assets/certs"),
        ("addon/BabelChat", "addon/BabelChat"),
    ],
    hiddenimports=[
        "lingua",
        "lingua.builder",
        "deepl",
        "watchdog",
        "watchdog.observers",
        "watchdog.events",
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "dotenv",
        "app.watcher",
        "app.parser",
        "app.detector",
        "app.translator",
        "app.cache",
        "app.pipeline",
        "app.overlay",
        "app.tray",
        "app.config",
        "app.settings_dialog",
        "app.setup_wizard",
        "app.about_dialog",
        "app.hotkeys",
        "app.hotkeys_windows",
        "app.text_utils",
        "app.memory_reader",
        "app.memory_reader_windows",
        "pymem",
        "pymem.process",
        "pymem.exception",
        "app.i18n",
        "app.phrasebook",
        "app.slang",
        "requests",
        # Screen (OCR) chat source
        "numpy",
        "PIL",
        "mss",
        "app.ocr_parse",
        "app.ocr_engine",
        "app.ocr_capture",
        "app.ocr_reader",
        "app.capture_source_qt",
        "app.region_picker_qt",
        *_ocr_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "pynput",
        "app.memory_reader_linux",
        "app.hotkeys_linux",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BabelChat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon="assets/icon.ico",
    onefile=True,
    # Reading another process owned by the same user needs PROCESS_VM_READ and
    # PROCESS_QUERY_INFORMATION, which Windows grants from the target's own
    # DACL. The scanner asks for exactly those. Elevation was only ever needed
    # by the pymem fallback's PROCESS_ALL_ACCESS open, which no longer happens —
    # and standing administrator rights turned an ordinary DLL-planting bug into
    # a privilege escalation.
    uac_admin=False,
)
