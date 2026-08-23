# BabelChat Rust Scanner

Replaces the pure-Python memory scanner with a compiled Rust library.

## Build

```fish
cd babelchat_scanner
cargo build --release
```

The library will be at `target/release/libbabelchat_scanner.so`.

## Install

Copy the built library into your `app/` directory:

```fish
cp target/release/libbabelchat_scanner.so ../app/
```

That is the whole install. `app/memory_reader_linux.py` already lives in the
repository — an earlier version of this file told you to copy it from here, and
that copy no longer exists.

BabelChat loads the library through `app/native_scanner.py`, which looks beside
the executable in a frozen build and beside `app/` otherwise. Every candidate is
an absolute path: passing a bare filename would send the loader through the
system search order, and anything a same-user process can write to would become
a place to plant a library. If the library is missing, the pure-Python scanner
takes over — slower, but the app still runs.

## Requirements

- Rust toolchain (`rustup` recommended)
- `ptrace_scope=0` as normal: `echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope`
