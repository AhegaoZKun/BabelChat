"""Platform dispatcher for memory reader.

Selects the correct implementation based on the current OS.
"""

import sys

if sys.platform == "win32":
    from app.memory_reader_windows import (  # noqa: F401
        MemoryChatWatcher,
        WoWAddonBufReader,
        MARKER_START,
        MARKER_END,
        POLL_INTERVAL,
        WOW_PROCESS_NAMES,
    )
else:
    from app.memory_reader_linux import (  # noqa: F401
        MemoryChatWatcher,
        WoWAddonBufReader,
        MARKER_START,
        MARKER_END,
        POLL_INTERVAL,
        WOW_PROCESS_NAMES,
    )
