"""Download the OCR recognition models for every script, ready to bundle.

The screen chat source reads text with a model per *script* (Latin, Cyrillic,
Korean, Japanese; Chinese and English ship inside the RapidOCR wheel). Without
this step a packaged build downloads the one it needs on first use, which means a
user with no network at that moment gets no translation until they are online.

Run it once on the build machine, before PyInstaller:

    python tools/fetch_ocr_models.py

It fills `ocr_models/`, which both build specs bundle. At run time the app uses
those files directly and only downloads for a script that is not there.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.ocr_engine import SCRIPTS, OcrUnavailable, create_engine, model_dir

    failed: list[str] = []
    for script in SCRIPTS:
        try:
            create_engine(script, "rapidocr")
        except OcrUnavailable as exc:
            failed.append(script)
            print(f"  {script:8s} FAILED  {exc}")
        else:
            print(f"  {script:8s} ok")

    print(f"\nmodels are in {model_dir().resolve()}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
