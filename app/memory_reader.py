"""Platform dispatcher for the memory reader.

Selects the correct implementation for the current OS. The frame markers come
from `addon_protocol` rather than from a platform module: they are part of the
addon's wire format and are identical everywhere, so re-exporting them per
platform only invited the two copies to drift.
"""

import sys

from app.addon_protocol import MARKER_END, MARKER_START  # noqa: F401  (re-export)

if sys.platform == "win32":
    from app.memory_reader_windows import (  # noqa: F401  (re-export)
        POLL_INTERVAL,
        WOW_PROCESS_NAMES,
        MemoryChatWatcher,
        WoWAddonBufReader,
    )
else:
    from app.memory_reader_linux import (  # noqa: F401  (re-export)
        POLL_INTERVAL,
        WOW_PROCESS_NAMES,
        MemoryChatWatcher,
        WoWAddonBufReader,
    )
