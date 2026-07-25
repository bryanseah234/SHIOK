"""JSON schema validation test for raw/manifest.json."""

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = PROJECT_ROOT / "raw" / "manifest.json"


def test_manifest_structure_if_exists() -> None:
    """Verify raw/manifest.json structure contains required keys if file exists."""
    if not MANIFEST_PATH.is_file():
        pytest.skip("raw/manifest.json does not exist yet")

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "generated_at" in data
    assert "sources" in data
    assert isinstance(data["sources"], dict)

    for source_key, entry in data["sources"].items():
        assert "source_name" in entry
        assert "url_as_discovered" in entry
        assert "sha256" in entry
        assert "bytes" in entry
        assert "etag" in entry
        assert "last_modified" in entry
        assert "fetched_at" in entry
        assert len(entry["sha256"]) == 64
