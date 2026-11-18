"""Both entry points must turn the saved settings into the same chat source.

The Qt (Windows) and GTK (Linux) entry points each build their own PipelineConfig,
and the GTK one cannot even be imported without a Wayland session. A name missing
from one of them is invisible to a compile check and fatal at start-up, so these
tests build the config through the real functions of both. GTK-bound modules are
replaced with empty stand-ins; nothing under test lives in them.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest

from app.config import AppConfig, capture_fields
from app.ocr_capture import OcrRegion


@pytest.fixture
def main_gtk(monkeypatch):
    """app.main_gtk imported with its GTK-only dependencies stubbed out."""
    stubs = {
        "app.overlay_gtk": {"ChatOverlayGtk": object},
        "app.settings_gtk": {"SettingsWindowGtk": object},
        "app.tray_sni": {"MenuItem": object, "TrayIcon": object},
    }
    for name, attrs in stubs.items():
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "app.main_gtk", raising=False)
    return importlib.import_module("app.main_gtk")


@pytest.fixture
def main_qt():
    pytest.importorskip("PyQt6")
    return importlib.import_module("app.main")


_REGION = OcrRegion(0.1, 0.6, 0.3, 0.3, monitor=0, px=(1920, 1080)).to_dict()


def _config(**overrides) -> AppConfig:
    base = {"own_language": "RU", "ocr_region": _REGION, "ocr_portal_token": "tok"}
    base.update(overrides)
    return AppConfig(**base)


@pytest.mark.parametrize("entry", ["main_gtk", "main_qt"])
def test_the_screen_source_reaches_the_pipeline_config(entry, request) -> None:
    module = request.getfixturevalue(entry)
    saved: list[str] = []
    built = module._build_pipeline_config(_config(), saved.append)
    assert built.capture_source == "ocr"
    assert built.ocr_region == _REGION
    assert built.ocr_portal_token == "tok"
    assert built.ocr_on_token is not None
    built.ocr_on_token("new-token")
    assert saved == ["new-token"]


@pytest.mark.parametrize("entry", ["main_gtk", "main_qt"])
def test_the_memory_source_reaches_the_pipeline_config(entry, request) -> None:
    module = request.getfixturevalue(entry)
    built = module._build_pipeline_config(_config(capture_source="memory"))
    assert built.capture_source == "memory"


@pytest.mark.parametrize("entry", ["main_gtk", "main_qt"])
def test_both_entry_points_describe_the_source_identically(entry, request) -> None:
    module = request.getfixturevalue(entry)
    config = _config()
    built = module._build_pipeline_config(config)
    expected = capture_fields(config)
    for key in ("capture_source", "ocr_region", "ocr_interval", "ocr_script", "ocr_portal_token"):
        assert getattr(built, key) == expected[key], key


@pytest.mark.parametrize(
    ("own_language", "script"),
    [("RU", "eslav"), ("DE", "latin"), ("KO", "korean"), ("JA", "japan"), ("EN", "default")],
)
def test_auto_script_follows_the_own_language(own_language: str, script: str) -> None:
    assert capture_fields(_config(own_language=own_language, ocr_script="auto"))["ocr_script"] == script


def test_an_explicit_script_is_not_overridden() -> None:
    assert capture_fields(_config(own_language="RU", ocr_script="korean"))["ocr_script"] == "korean"


def test_the_region_is_copied_not_shared() -> None:
    config = _config()
    fields = capture_fields(config)
    fields["ocr_region"]["x"] = 0.99
    assert config.ocr_region["x"] == _REGION["x"]
