"""Fetch and hash pipeline module for S.H.I.O.K. Index (T0.3)."""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml  # type: ignore[import-untyped]
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "pipeline" / "config" / "sources.yaml"
RAW_DIR = PROJECT_ROOT / "raw"
MANIFEST_PATH = RAW_DIR / "manifest.json"
TMP_DIR = RAW_DIR / "tmp"

USER_AGENT = "SHIOK-Index-Pipeline/1.0 (Singapore Walk-to-Transit Index)"
MAX_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB limit


def get_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if extra:
        headers.update(extra)
    return headers


def get_datamall_headers() -> dict[str, str]:
    headers = get_headers()
    account_key = os.getenv("LTA_DATAMALL_ACCOUNT_KEY", "")
    if account_key:
        headers["AccountKey"] = account_key
    return headers


def load_sources() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    sources: dict[str, Any] = data.get("sources", {})
    return sources


def load_manifest() -> dict[str, Any]:
    if MANIFEST_PATH.is_file():
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                manifest_data: dict[str, Any] = json.load(f)
                return manifest_data
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: Failed to load manifest from {MANIFEST_PATH}: {e}")
    return {"generated_at": None, "sources": {}}


def save_manifest(manifest: dict[str, Any]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


def canonicalize_json(records: list[dict[str, Any]], sort_key: str) -> bytes:
    """Canonicalize JSON array by sorting record keys and sorting records by primary key."""
    sorted_records = sorted(records, key=lambda r: str(r.get(sort_key, "")))
    canonical_str = json.dumps(sorted_records, sort_keys=True, separators=(",", ":"))
    return canonical_str.encode("utf-8")


def fetch_datamall_api(endpoint: str, key_name: str) -> tuple[bytes, str]:
    """Fetch paginated DataMall API endpoint ($skip 500) and canonicalize."""
    headers = get_datamall_headers()
    all_records: list[dict[str, Any]] = []
    skip = 0
    client = httpx.Client(timeout=60.0, follow_redirects=True)

    try:
        while True:
            url = f"{endpoint}?$skip={skip}"
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            value = data.get("value", [])
            if not value:
                break
            all_records.extend(value)
            skip += len(value)
            time.sleep(0.2)  # politeness throttle
    finally:
        client.close()

    # Determine sort key
    sort_key = "BusStopCode" if "BusStops" in endpoint else "ServiceNo"
    payload_bytes = canonicalize_json(all_records, sort_key)
    return payload_bytes, endpoint


def resolve_datagov_download_url(dataset_id: str) -> str:
    """Resolve data.gov.sg dataset download URL via initiate-download API."""
    url = f"https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/initiate-download"
    headers = get_headers()
    client = httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        res_json = resp.json()
        download_url: str = str(res_json.get("data", {}).get("url", ""))
        if not download_url:
            raise ValueError(f"No download URL returned for dataset {dataset_id}")
        return download_url
    finally:
        client.close()


def run_check(sources: dict[str, Any]) -> int:
    manifest = load_manifest()
    existing_sources: dict[str, Any] = manifest.get("sources", {})

    total_sources = len(sources)
    checked_count = 0
    unchanged_count = 0
    changed_count = 0
    error_count = 0
    unresolved_count = 0

    print("Checking upstream datasets for changes...")

    for key, spec in sources.items():
        kind = spec.get("kind")
        name = spec.get("name")
        current_entry: dict[str, Any] = existing_sources.get(key, {})

        if kind == "datagov_polldownload":
            dataset_id = spec.get("dataset_id")
            if dataset_id == "UNRESOLVED — runtime discovery":
                unresolved_count += 1
                print(f"[{key}] {name}: Skipped (runtime discovery unresolved)")
                continue

            checked_count += 1
            try:
                download_url = resolve_datagov_download_url(dataset_id)
                headers = get_headers()
                if current_entry.get("etag"):
                    headers["If-None-Match"] = current_entry["etag"]
                if current_entry.get("last_modified"):
                    headers["If-Modified-Since"] = current_entry["last_modified"]

                with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                    resp = client.get(download_url, headers=headers)
                    if resp.status_code == 304:
                        unchanged_count += 1
                        print(f"[{key}] {name}: unchanged (304 Not Modified)")
                        continue

                    resp.raise_for_status()
                    content = resp.content

                    sha256 = hashlib.sha256(content).hexdigest()
                    if current_entry.get("sha256") != sha256:
                        changed_count += 1
                        print(f"[{key}] {name}: CHANGED (hash mismatch)")
                    else:
                        unchanged_count += 1
                        print(f"[{key}] {name}: unchanged")
            except (httpx.HTTPError, ValueError, OSError) as e:
                error_count += 1
                print(f"[{key}] {name}: Error during check: {e}")

        elif kind == "datamall_api_paginated":
            checked_count += 1
            try:
                endpoint = spec.get("endpoint", "")
                payload_bytes, _ = fetch_datamall_api(endpoint, key)
                sha256 = hashlib.sha256(payload_bytes).hexdigest()
                if current_entry.get("sha256") != sha256:
                    changed_count += 1
                    print(f"[{key}] {name}: CHANGED (hash mismatch)")
                else:
                    unchanged_count += 1
                    print(f"[{key}] {name}: unchanged")
            except (httpx.HTTPError, ValueError, OSError) as e:
                error_count += 1
                print(f"[{key}] {name}: Error during check: {e}")

        else:
            unresolved_count += 1
            print(f"[{key}] {name}: Stub check (listing/probe required)")

    print(
        f"Summary: checked {checked_count}/{total_sources}, unchanged {unchanged_count}, changed {changed_count}, errors {error_count}, unresolved {unresolved_count}"
    )

    if error_count > 0 or changed_count > 0:
        return 1
    return 0


def run_ingest(sources: dict[str, Any]) -> int:
    manifest = load_manifest()
    manifest_sources: dict[str, Any] = manifest.setdefault("sources", {})
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    print("Ingesting upstream datasets...")

    for key, spec in sources.items():
        kind = spec.get("kind")
        name = spec.get("name")

        if kind == "datagov_polldownload":
            dataset_id = spec.get("dataset_id")
            if dataset_id == "UNRESOLVED — runtime discovery":
                continue

            try:
                download_url = resolve_datagov_download_url(dataset_id)
                with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                    resp = client.get(download_url, headers=get_headers())
                    resp.raise_for_status()
                    content = resp.content

                    if len(content) > MAX_SIZE_BYTES:
                        raise ValueError(f"Download size {len(content)} exceeds 500 MB limit")

                    sha256 = hashlib.sha256(content).hexdigest()
                    etag = resp.headers.get("ETag", "")
                    last_modified = resp.headers.get("Last-Modified", "")

                    target_dir = RAW_DIR / sha256
                    target_dir.mkdir(parents=True, exist_ok=True)
                    filename = f"{key}.geojson"
                    target_path = target_dir / filename

                    with open(target_path, "wb") as f:
                        f.write(content)

                    manifest_sources[key] = {
                        "source_name": name,
                        "url_as_discovered": download_url,
                        "sha256": sha256,
                        "bytes": len(content),
                        "etag": etag,
                        "last_modified": last_modified,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                    print(
                        f"[{key}] Ingested {name} -> raw/{sha256[:8]}.../{filename} ({len(content)} bytes)"
                    )
            except (httpx.HTTPError, ValueError, OSError) as e:
                print(f"[{key}] Error ingesting {name}: {e}")

        elif kind == "datamall_api_paginated":
            try:
                endpoint = spec.get("endpoint", "")
                payload_bytes, download_url = fetch_datamall_api(endpoint, key)
                sha256 = hashlib.sha256(payload_bytes).hexdigest()

                target_dir = RAW_DIR / sha256
                target_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{key}.json"
                target_path = target_dir / filename

                with open(target_path, "wb") as f:
                    f.write(payload_bytes)

                manifest_sources[key] = {
                    "source_name": name,
                    "url_as_discovered": download_url,
                    "sha256": sha256,
                    "bytes": len(payload_bytes),
                    "etag": "",
                    "last_modified": "",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                print(
                    f"[{key}] Ingested {name} -> raw/{sha256[:8]}.../{filename} ({len(payload_bytes)} bytes)"
                )
            except (httpx.HTTPError, ValueError, OSError) as e:
                print(f"[{key}] Error ingesting {name}: {e}")

    save_manifest(manifest)
    print("Manifest updated successfully.")
    return 0


def main(action: str) -> int:
    sources = load_sources()
    if action == "check":
        return run_check(sources)
    elif action == "ingest":
        return run_ingest(sources)
    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "check"
    sys.exit(main(action))
