"""Platform dispatcher for memory reader.

Selects the correct implementation based on the current OS.
"""

import sys

if sys.platform == "win32":
    from app.memory_reader_windows import (  # noqa: F401
        MARKER_END,
        MARKER_START,
        POLL_INTERVAL,
        WOW_PROCESS_NAMES,
        MemoryChatWatcher,
        WoWAddonBufReader,
    )
else:
    from app.memory_reader_linux import (  # noqa: F401
        MARKER_END,
        MARKER_START,
        POLL_INTERVAL,
        WOW_PROCESS_NAMES,
        MemoryChatWatcher,
        WoWAddonBufReader,
    )
