"""Text recognition behind one small interface.

Two engines, tried in order: RapidOCR (ONNX, pure pip, ships its Chinese/English
models in the wheel) and Tesseract (system binary, optional). Both return the
same thing — boxes with text and confidence — so everything downstream
(`app.ocr_parse`) is independent of which one is running.

Recognition models are per *script*, not per language: one model reads every
language written in Latin letters, another reads Cyrillic, and so on. The script
is chosen from the user's own language, because that is the language their chat
is overwhelmingly displayed in. Text in a script the chosen model does not cover
is read badly or not at all, and that is the honest limit of this source.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Protocol

import numpy as np

from app.ocr_parse import Box

logger = logging.getLogger(__name__)

#: Scripts the app can read, as RapidOCR names them.
SCRIPTS = ("default", "latin", "eslav", "korean", "japan")

_SCRIPT_FOR_LANGUAGE = {
    "RU": "eslav", "UK": "eslav", "BE": "eslav",
    "ES": "latin", "DE": "latin", "FR": "latin", "PT": "latin", "IT": "latin", "PL": "latin",
    "KO": "korean",
    "JA": "japan",
    # English and Chinese are the bundled model's own languages.
    "EN": "default", "ZH": "default",
}

# Tesseract language packs per script. A pack that is not installed is dropped.
_TESSERACT_LANGS = {
    "default": ("eng", "chi_sim"),
    "latin": ("eng", "spa", "deu", "fra", "por", "ita", "pol"),
    "eslav": ("rus", "eng"),
    "korean": ("kor", "eng"),
    "japan": ("jpn", "eng"),
}


class OcrUnavailable(RuntimeError):
    """No usable recognition engine on this machine."""


class OcrEngine(Protocol):
    name: str

    def recognise(self, image: np.ndarray) -> list[Box]:
        """`image` is an RGB uint8 array (H, W, 3)."""
        ...


def script_for_language(code: str) -> str:
    return _SCRIPT_FOR_LANGUAGE.get((code or "").upper(), "default")


def model_dir() -> Path:
    """Where downloaded recognition models live. Same convention as the config."""
    path = (
        Path.home() / ".config" / "BabelChat" / "ocr_models"
        if getattr(sys, "frozen", False)
        else Path("ocr_models")
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _seed_model_dir(target: Path) -> None:
    """Copy the models that ship inside the rapidocr wheel into `target`.

    Pointing RapidOCR at our own model folder (needed so downloaded per-script
    models persist, and so a packaged build does not re-download into a temporary
    directory on every launch) also moves where it looks for the models the wheel
    already contains. Seeding the folder keeps the default path working offline.
    """
    try:
        import rapidocr
    except ImportError:
        return
    shipped = Path(rapidocr.__file__).resolve().parent / "models"
    if not shipped.is_dir():
        return
    for model in shipped.glob("*.onnx"):
        dest = target / model.name
        if not dest.exists():
            try:
                shutil.copyfile(model, dest)
            except OSError:
                logger.debug("could not seed %s", model.name, exc_info=True)


def _bundled_model(script: str) -> Path | None:
    """A recognition model shipped inside the build, if there is one.

    `tools/fetch_ocr_models.py` puts them in `ocr_models/` before packaging, so a
    release works offline. Without them RapidOCR downloads the model on first
    use, which needs the network once.
    """
    roots = [Path(getattr(sys, "_MEIPASS", "")) / "ocr_models", Path(__file__).resolve().parent.parent / "ocr_models"]
    for root in roots:
        if not root.is_dir():
            continue
        matches = sorted(root.glob(f"{script}_PP-OCR*_rec_*.onnx"))
        if matches:
            return matches[-1]
    return None


# ── RapidOCR ─────────────────────────────────────────────────────────────────


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self, script: str = "default") -> None:
        try:
            from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise OcrUnavailable("rapidocr is not installed (pip install rapidocr onnxruntime)") from exc

        models = model_dir()
        _seed_model_dir(models)
        params: dict[str, object] = {
            "Global.log_level": "warning",
            "Global.model_root_dir": str(models),
            # Chat is horizontal. The angle classifier would only cost time.
            "Global.use_cls": False,
            # The defaults scale a SHORT image's smaller side up to 736 px. A chat
            # crop is a wide, short strip, so that inflates it several-fold for no
            # gain: same text found, an order of magnitude slower. Cap the long
            # side instead and leave small crops at their own size.
            "Det.limit_type": "max",
            "Det.limit_side_len": 1280,
            # Two threads is plenty for a strip this size and leaves the game its cores.
            "EngineConfig.onnxruntime.intra_op_num_threads": 2,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }
        if script != "default":
            lang = {
                "latin": LangRec.LATIN, "eslav": LangRec.ESLAV,
                "korean": LangRec.KOREAN, "japan": LangRec.JAPAN,
            }[script]
            params["Rec.lang_type"] = lang
            params["Rec.ocr_version"] = OCRVersion.PPOCRV5
            params["Rec.model_type"] = ModelType.MOBILE
            bundled = _bundled_model(script)
            if bundled is not None:
                params["Rec.model_path"] = str(bundled)

        try:
            self._engine = RapidOCR(params=params)
        except Exception as exc:  # noqa: BLE001 - model fetch / onnxruntime problems
            raise OcrUnavailable(f"RapidOCR could not start for script '{script}': {exc}") from exc
        self.script = script

    def recognise(self, image: np.ndarray) -> list[Box]:
        result = self._engine(np.ascontiguousarray(image[:, :, ::-1]))  # RGB -> BGR
        if result is None or result.boxes is None or result.txts is None:
            return []
        boxes: list[Box] = []
        for quad, text, score in zip(result.boxes, result.txts, result.scores, strict=False):
            xs = [float(p[0]) for p in quad]
            ys = [float(p[1]) for p in quad]
            boxes.append((min(xs), min(ys), max(xs), max(ys), str(text), float(score)))
        return boxes


# ── Tesseract ────────────────────────────────────────────────────────────────


class TesseractEngine:
    name = "tesseract"

    def __init__(self, script: str = "default") -> None:
        try:
            import pytesseract
        except ImportError as exc:
            raise OcrUnavailable("pytesseract is not installed") from exc
        if not shutil.which("tesseract"):
            raise OcrUnavailable("the tesseract binary is not on PATH")
        self._tess = pytesseract
        try:
            installed = set(pytesseract.get_languages(config=""))
        except Exception:  # noqa: BLE001
            installed = {"eng"}
        wanted = [lang for lang in _TESSERACT_LANGS.get(script, ("eng",)) if lang in installed]
        self._lang = "+".join(wanted) or "eng"
        self.script = script

    def recognise(self, image: np.ndarray) -> list[Box]:
        from PIL import Image

        data = self._tess.image_to_data(
            Image.fromarray(image),
            lang=self._lang,
            config="--psm 6",
            output_type=self._tess.Output.DICT,
        )
        boxes: list[Box] = []
        for i, text in enumerate(data["text"]):
            conf = float(data["conf"][i])
            if conf < 0 or not str(text).strip():
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            boxes.append((float(x), float(y), float(x + w), float(y + h), str(text), conf / 100.0))
        return boxes


# ── selection ────────────────────────────────────────────────────────────────


def create_engine(script: str = "default", prefer: str = "auto") -> OcrEngine:
    """The best engine this machine can run, or OcrUnavailable with the reasons."""
    order = {
        "rapidocr": (RapidOcrEngine,),
        "tesseract": (TesseractEngine,),
    }.get(prefer, (RapidOcrEngine, TesseractEngine))

    failures: list[str] = []
    for factory in order:
        try:
            engine = factory(script)
        except OcrUnavailable as exc:
            failures.append(f"{factory.__name__}: {exc}")
            continue
        logger.info("OCR engine: %s (script=%s)", engine.name, script)
        return engine
    raise OcrUnavailable("; ".join(failures) or "no OCR engine configured")


def prepare(image: np.ndarray, target_width: int = 900, max_scale: int = 3) -> np.ndarray:
    """Enlarge a small crop so game-sized text (12-14 px) is legible to the model."""
    height, width = image.shape[:2]
    if width <= 0 or width >= target_width:
        return image
    scale = min(max_scale, max(1, round(target_width / width)))
    if scale == 1:
        return image
    from PIL import Image

    return np.asarray(Image.fromarray(image).resize((width * scale, height * scale), Image.LANCZOS))


def is_available() -> bool:
    """Cheap check for the wizard: can an engine possibly be created here?"""
    try:
        import rapidocr  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import pytesseract  # noqa: F401
        return shutil.which("tesseract") is not None
    except ImportError:
        return False


# Keep the environment quiet: onnxruntime and rapidocr both log at INFO by default.
os.environ.setdefault("ORT_LOGGING_LEVEL", "3")
