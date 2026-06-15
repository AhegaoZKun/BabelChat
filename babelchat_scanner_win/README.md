# BabelChat Windows Rust Scanner

Replaces the pure-Python pymem memory scanner with a compiled Rust DLL.

## Build

Requires Rust toolchain and a Windows machine (or cross-compilation).

```cmd
cd babelchat_scanner_win
cargo build --release
```

The DLL will be at `target\release\babelchat_scanner_win.dll`.

## Install

```cmd
copy target\release\babelchat_scanner_win.dll ..\app\
copy memory_reader_windows.py ..\app\
```

Then rebuild with PyInstaller — add to `build.spec`:

```python
binaries=[
    ('app/babelchat_scanner_win.dll', '.'),
    ...
],
```

## Fallback

If `babelchat_scanner_win.dll` is not found, the app automatically falls back
to the pure-Python pymem scanner. No functionality is lost.

## Requirements

- Rust toolchain (rustup)
- Run BabelChat as Administrator (required for ReadProcessMemory)
