"""Forever addon manifest compatibility checks."""
from pathlib import Path


def test_forever_manifest_targets_interface_16001() -> None:
    manifest = Path("addon/BabelChat/BabelChat_Camelot.toc")
    assert manifest.exists()
    contents = manifest.read_text(encoding="utf-8")
    assert contents.splitlines()[0] == "## Interface: 16001"
    assert "CompanionBuffer.lua" in contents
    assert "Core.lua" in contents
