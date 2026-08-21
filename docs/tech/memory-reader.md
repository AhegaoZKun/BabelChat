# Memory Reader

## Why Memory Reading?

WoW writes chat to `WoWChatLog.txt`, but uses an internal ~4KB buffer. The file updates only when the buffer fills — **real delay is 1-5 minutes**. Unacceptable for a chat translator.

Instead, we read the addon's Lua SavedVariable directly from WoW's process memory via `ReadProcessMemory`. Latency: **<1 second**.

## How It Works

The addon stores messages in `BabelChatDB.wctbuf` — a Lua string with markers:

```
__WCT_BUF_0042__
0|META|PLAYER|Thrall-Sargeras
1|RAW|SAY|Thrall-Sargeras|Hello everyone
2|DICT|GUILD|Jaina-Server|some text\ttranslated text
__WCT_END__
```

The companion app scans WoW's memory for these markers, then reads the content between them.

## How the buffer is found

`app/native_scanner.py` loads a small Rust library — `babelchat_scanner_win.dll`
on Windows, `libbabelchat_scanner.so` on Linux — from an absolute path. It scans
the game process for the `__WCT_BUF_` marker and reads the buffer back.

If that library is missing or unloadable, `_pymem_find_buffer` does the same job
in pure Python: one `pattern_scan_all` for the marker, then the candidate with
the highest sequence number wins. Slower, but the app keeps working.

The process is opened with `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION` only.
Reading a process owned by the same user needs nothing more, which is why the
app does not ask for administrator rights.

> An earlier version of this document described a "tiered scan cascade"
> (cached region → history → neighbourhood → heap → full scan) and a "zombie
> buffer" blacklist with a 60-second TTL. Neither exists in the Python code
> today; the tiering such as it is lives inside the Rust scanner.

## The record format

`app/addon_protocol.py` owns it — both platform readers import from there, after
a period where each carried its own copy and they drifted apart.

```
__WCT_BUF_NNNN__
0|META|PLAYER|Name-Realm
17|RAW|SAY|Thrall-Sargeras|hello everyone
18|DICT|CHANNEL:2:Торговля - Оргриммар|Vasya|wts crest
__WCT_END__
```

`NNNN` is the sequence counter modulo 10000 — padding for a quick staleness
check; the authoritative number opens each record. `KIND` is `RAW` or `DICT`,
where `DICT` means the addon also glossed the line in the player's own chat.
A public channel carries its type id before its name: the id is the same on
every locale, and it is 0 for a channel a player made.

Fields are sanitised by the addon before they get here — no record can contain
a newline, a tab, or the frame markers. That last one matters: anyone can type
`__WCT_END__` into Trade chat, and the reader finds the end of the buffer by
scanning for exactly that string.

## ToS Compliance

`ReadProcessMemory` is read-only. Warden (WoW anti-cheat) does not flag external read-only access.

Note: WeakAuras Companion and WarcraftLogs use file-based approaches (SavedVariables and combat log tailing). BabelChat's direct memory reading is unique — it's the only method that achieves sub-second chat latency, since WoW's chat log (`WoWChatLog.txt`) buffers writes with ~4KB buffer and flushes unpredictably (1-5+ minute delays, messages arrive in random-order bursts).
