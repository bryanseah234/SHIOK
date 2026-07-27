import hashlib
import json
from pathlib import Path

from pipeline.network_preflight import build_network_preflight, source_file_status


def write_raw_source(raw_dir: Path, source_key: str, filename: str, content: bytes) -> dict:
    digest = hashlib.sha256(content).hexdigest()
    source_dir = raw_dir / digest
    source_dir.mkdir(parents=True)
    (source_dir / filename).write_bytes(content)
    return {
        "source_key": source_key,
        "source_name": source_key,
        "url_as_discovered": None,
        "sha256": digest,
        "bytes": len(content),
        "etag": None,
        "last_modified": None,
        "fetched_at": "2026-07-27T10:00:00+00:00",
    }


def write_manifest(path: Path, entries: dict[str, dict]) -> None:
    payload = {
        "generated_at": "2026-07-27T10:00:00+00:00",
        "sources": entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def required_raw_manifest(tmp_path: Path) -> tuple[Path, Path]:
    raw_dir = tmp_path / "raw"
    entries = {}
    for source_key, filename in {
        "planning_area_boundary": "planning_area_boundary.geojson",
        "covered_linkway": "covered_linkway.zip",
        "osm_extract": "osm_extract.osm.pbf",
    }.items():
        entries[source_key] = write_raw_source(
            raw_dir,
            source_key,
            filename,
            f"{source_key} fixture".encode("utf-8"),
        )
    manifest_path = raw_dir / "manifest.json"
    write_manifest(manifest_path, entries)
    return raw_dir, manifest_path


def test_source_file_status_verifies_manifest_path_and_hash(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    entry = write_raw_source(
        raw_dir,
        "osm_extract",
        "osm_extract.osm.pbf",
        b"osm fixture",
    )

    status = source_file_status(
        source_key="osm_extract",
        filename="osm_extract.osm.pbf",
        raw_dir=raw_dir,
        manifest_sources={"osm_extract": entry},
    )

    assert status["present"] is True
    assert status["hash_ok"] is True
    assert status["errors"] == []


def test_network_preflight_accepts_raw_inputs_without_starting_island_build(tmp_path: Path):
    raw_dir, manifest_path = required_raw_manifest(tmp_path)

    ok, report = build_network_preflight(
        area="island",
        raw_dir=raw_dir,
        manifest_path=manifest_path,
        qa_dir=tmp_path / "qa",
        processed_dir=tmp_path / "processed",
        inspect_geometries=False,
    )

    assert ok, report
    assert report["checkpoint"]["human_approval_required_before_build"] is True
    assert report["checkpoint"]["build_started"] is False
    assert report["checkpoint"]["build_allowed_now"] is False
    assert report["checkpoint"]["can_run_after_human_approval"] is True
    assert report["outputs"]["qa_json"]["exists"] is False


def test_network_preflight_rejects_missing_required_manifest_source(tmp_path: Path):
    raw_dir, manifest_path = required_raw_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["sources"]["covered_linkway"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    ok, report = build_network_preflight(
        area="island",
        raw_dir=raw_dir,
        manifest_path=manifest_path,
        qa_dir=tmp_path / "qa",
        processed_dir=tmp_path / "processed",
        inspect_geometries=False,
    )

    assert not ok
    assert "missing manifest source: covered_linkway" in report["errors"]
    assert report["checkpoint"]["can_run_after_human_approval"] is False


def test_network_preflight_rejects_hash_mismatch(tmp_path: Path):
    raw_dir, manifest_path = required_raw_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_hash = manifest["sources"]["planning_area_boundary"]["sha256"]
    path = raw_dir / expected_hash / "planning_area_boundary.geojson"
    path.write_text("tampered", encoding="utf-8")

    ok, report = build_network_preflight(
        area="island",
        raw_dir=raw_dir,
        manifest_path=manifest_path,
        qa_dir=tmp_path / "qa",
        processed_dir=tmp_path / "processed",
        inspect_geometries=False,
    )

    assert not ok
    assert any(
        error.startswith("hash mismatch for planning_area_boundary") for error in report["errors"]
    )
