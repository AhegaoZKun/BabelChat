# BabelChat Rust Scanner

Replaces the pure-Python memory scanner with a compiled Rust library.

## Build

```fish
cd babelchat_scanner
cargo build --release
```

The library will be at `target/release/libbabelchat_scanner.so`.

## Install

Copy both files into your `app/` directory:

```fish
cp target/release/libbabelchat_scanner.so ../app/
cp memory_reader_linux.py ../app/
```

BabelChat will automatically use the Rust library if it finds
`libbabelchat_scanner.so` next to `memory_reader_linux.py`.

## Requirements

- Rust toolchain (`rustup` recommended)
- `ptrace_scope=0` as normal: `echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope`
