# Memory Reader

## Why memory at all

WoW writes chat to `WoWChatLog.txt` behind an internal buffer of a few
kilobytes, and flushes it by volume rather than by time. Chat is low volume, so
the real delay is minutes. Unusable for a chat translator.

So the companion reads the addon's SavedVariable straight out of the game's
process. Latency is one poll — 250 ms.

## What it reads

The addon keeps messages in `BabelChatDB.wctbuf`, a Lua string framed by
markers:

```
__WCT_BUF_0042__
0|META|FLUSH|1731
0|META|LOCKED|0
0|META|PLAYER|Thrall-Sargeras
1|RAW|SAY|Thrall-Sargeras|Hello everyone
2|DICT|GUILD|Jaina-Server|some text
__WCT_END__
```

`FLUSH` is the pulse — see below. `LOCKED` is 1 while the game is refusing the
addon chat text, which it does for the duration of a mythic keystone run.

## How the buffer is found — and why not by searching

**A Lua string is immutable, so every rebuild allocates a new one somewhere
else.** This is the single fact the whole design turns on. Measured on a live
game: fourteen consecutive rebuilds landed in fourteen different memory regions,
scattered across twenty gigabytes of address space, never once reusing a region
it had already used.

Anything that *searches* for the buffer therefore pays a sweep of the heap per
rebuild. The 3.3 release did exactly that and the arithmetic was brutal: 48% of
one core, measured, while still delivering five messages a minute before going
deaf on a copy the addon had abandoned.

### The anchor

The addon parks a constant in its saved table — `BabelChatDB.wctAnchor`, a
number that is written once at load and never changes.

A constant can be searched for at leisure, because it does not move while you
are looking for it. And a Lua table's storage does not move at all while the
table does not rehash — which the addon prevents by declaring every key it will
ever use during `PreallocateCompanionKeys`.

So: find the anchor once (about two seconds), look in the few kilobytes around
it for a slot holding a pointer to something that starts with `__WCT_BUF_`, and
keep that slot's address. From then on every poll is two reads — eight bytes for
the pointer, then the buffer at the far end of it — and the answer is the live
buffer by construction rather than by inference.

Measured after: **0.1% of one core, zero sweeps, messages in the poll they were
sent in.**

### The pulse

`0|META|FLUSH|<n>` is a counter the addon increments on every rebuild, including
the rebuilds it performs every two seconds when nobody is saying anything.

It exists because a freed Lua string stays readable for a while, and a copy the
addon will never write to again is otherwise **indistinguishable from a quiet
chat**: same markers, same last message. Every version of this reader before the
pulse settled on such a copy sooner or later and went silent for minutes.

With it, a slot whose pulse has stopped for six seconds is known to be dead, and
the reader goes looking for the live table. That is also how a table left behind
by `/reload` is spotted.

### The fallback

An addon older than the anchor has no constant to find. For those the scanner
sweeps memory for the marker, in parallel, keeping the candidate with the highest
pulse — or, with no pulse at all, the highest message number. It is slow and it
is why the anchor exists, but the app and the addon are installed separately and
one running ahead of the other has to keep working.

`app/memory_scan_windows.py` falls back further still, to a pure-Python scan
through `pymem`, if the native library cannot be loaded at all.

## Cost and courtesy

The process is opened with `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION` and
nothing more. Reading a process owned by the same user needs no more than that,
which is why the app does not ask for administrator rights — and why it stopped:
standing elevation turned an ordinary DLL-planting bug into a privilege
escalation.

Scan threads run at `THREAD_PRIORITY_IDLE` on Windows and `SCHED_IDLE` on Linux,
so what work there is lands between the game's frames rather than in them.

Process handles are owned values (`OwnedHandle`) that close themselves. The
parallel scans open one per worker thread, and before that they leaked four per
scan — enough, over a long session of the old continuously-scanning design, to
exhaust the handle table and have `OpenProcess` start refusing.

## Seeing what it is doing

`describe_state` reports the scanner's own situation, and the reader writes it
into the log every fifteen seconds of silence:

```
cached=1 addr=0x19167de7a60 pulse=598 quiet_ms=0 scans=3 slot=0x19174395098+32
```

`scans=0` in normal operation is the point: it means the anchor path is working
and nothing is being searched for. Every hour spent debugging this scanner
before that line existed was spent inferring those numbers from the outside.

## What was tried and does not work

- **Padding the buffer to a fixed length**, so the allocator would hand back the
  block it had just freed. Nineteen addresses in two minutes: Lua interns
  strings, and the old one is still alive when the new one is asked for.
- **Remembering the regions the buffer has lived in** and looking there first —
  the tier the Rust rewrite dropped from the original Python reader. The buffer
  never returns to a region it has used, so it missed every time, at 180% of a
  core.
- **Following a pointer to the string itself.** Exactly one aligned pointer to
  it exists anywhere in the process, and it does not track the buffer. Only the
  table slot does.
- **Reading chat during a mythic keystone run.** The game hands the text to
  addons as a secret value: it reports as a string and raises on every
  operation. Nothing can read it. The only thing to do is say so, which the
  addon and the indicator now do.
