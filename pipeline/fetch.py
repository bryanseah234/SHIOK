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


def resolve_datagov_download_url(dataset_id: str) -> str:
    """Resolve data.gov.sg dataset download URL via initiate-download API with retry logic."""
    url = f"https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/initiate-download"
    headers = get_headers()
    client = httpx.Client(timeout=30.0, follow_redirects=True)

    for attempt in range(1, 4):
        time.sleep(2.5 * attempt)
        try:
            resp = client.get(url, headers=headers)
            if resp.status_code == 429:
                print(
                    f"Rate limited (429) on data.gov.sg, retrying in {3.0 * attempt}s (attempt {attempt}/3)..."
                )
                continue
            resp.raise_for_status()
            res_json = resp.json()
            download_url: str = str(res_json.get("data", {}).get("url", ""))
            if not download_url:
                raise ValueError(f"No download URL returned for dataset {dataset_id}")
            return download_url
        except Exception as e:
            if attempt == 3:
                client.close()
                raise e
    client.close()
    raise ValueError(f"Failed to initiate download for dataset {dataset_id} after 3 attempts")


def resolve_datamall_static_url(keyword: str) -> str:
    from datetime import datetime, timedelta

    # Prefix discovery: try current month, then previous months up to 6 months back
    now = datetime.now()
    client = httpx.Client(timeout=10.0, follow_redirects=True)

    for i in range(6):
        d = now - timedelta(days=30 * i)
        suffix = d.strftime("%b%Y")  # e.g. Jul2026
        url = f"https://datamall.lta.gov.sg/content/dam/datamall/datasets/Geospatial/{keyword}_{suffix}.zip"
        try:
            resp = client.head(url, headers=get_headers())
            if resp.status_code == 200:
                client.close()
                return url
        except httpx.RequestError:
            pass

    client.close()
    raise ValueError(f"Unauthenticated static prefix discovery failed for keyword: {keyword}")


def resolve_datamall_geospatial_url(keyword: str) -> str:
    try:
        url = resolve_datamall_static_url(keyword)
        print(f"Discovered unauthenticated static URL for {keyword}: {url}")
        return url
    except Exception as e:
        print(
            f"Unauthenticated static discovery failed for {keyword}: {e}. Falling back to Authenticated GeospatialWholeIsland API."
        )

    url = f"https://datamall2.mytransport.sg/ltaodataservice/GeospatialWholeIsland?ID={keyword}"
    headers = get_datamall_headers()
    if "AccountKey" not in headers:
        raise ValueError("LTA_DATAMALL_ACCOUNT_KEY missing for geospatial discovery")

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        value = data.get("value", [])
        if not value:
            raise ValueError(f"No geospatial link found for keyword: {keyword}")
        return str(value[0].get("Link", ""))


def run_check(sources: dict[str, Any]) -> int:
    manifest = load_manifest()
    existing_sources: dict[str, Any] = manifest.get("sources", {})

    total_sources = len(sources)
    checked_count = 0
    unchanged_count = 0
    changed_count = 0
    error_count = 0
    unresolved_count = 0
    blocked_count = 0

    account_key = os.getenv("LTA_DATAMALL_ACCOUNT_KEY", "")

    print("Checking upstream datasets for changes...")

    for key, spec in sources.items():
        kind = spec.get("kind")
        name = spec.get("name")
        current_entry: dict[str, Any] = existing_sources.get(key, {})

        if kind == "datamall_api_paginated":
            if not account_key:
                blocked_count += 1
                print(
                    f"[{key}] {name}: BLOCKED — owner key pending (no LTA_DATAMALL_ACCOUNT_KEY in .env)"
                )
                continue

            endpoint = spec.get("endpoint", "")
            try:
                with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                    resp = client.get(endpoint, headers=get_datamall_headers())
                    if resp.status_code == 401:
                        blocked_count += 1
                        print(
                            f"[{key}] {name}: BLOCKED — owner key pending (401 Unauthorized from DataMall)"
                        )
                        continue
                    elif resp.status_code == 404:
                        blocked_count += 1
                        print(
                            f"[{key}] {name}: BLOCKED — owner key pending (404 Not Found from DataMall)"
                        )
                        continue
                    resp.raise_for_status()
            except (httpx.HTTPError, ValueError, OSError):
                blocked_count += 1
                print(f"[{key}] {name}: BLOCKED — owner key pending")
                continue

            checked_count += 1
            unchanged_count += 1

        elif kind == "datamall_geospatial_listing":
            keyword = spec.get("search_keyword", "")
            try:
                url = resolve_datamall_geospatial_url(keyword)
            except ValueError as e:
                if "LTA_DATAMALL_ACCOUNT_KEY missing" in str(e):
                    blocked_count += 1
                    print(
                        f"[{key}] {name}: BLOCKED — owner key pending (no LTA_DATAMALL_ACCOUNT_KEY in .env)"
                    )
                    continue
                else:
                    error_count += 1
                    print(f"[{key}] {name}: Error discovering url: {e}")
                    continue
            except httpx.HTTPError as e:
                if getattr(e, "response", None) and e.response.status_code == 401:
                    blocked_count += 1
                    print(
                        f"[{key}] {name}: BLOCKED — owner key pending (401 Unauthorized from DataMall)"
                    )
                    continue
                error_count += 1
                print(f"[{key}] {name}: Error discovering url: {e}")
                continue

            checked_count += 1
            try:
                with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                    headers = get_headers()
                    if current_entry.get("etag"):
                        headers["If-None-Match"] = current_entry["etag"]
                    if current_entry.get("last_modified"):
                        headers["If-Modified-Since"] = current_entry["last_modified"]

                    resp = client.get(url, headers=headers)
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

        elif kind == "datagov_polldownload":
            dataset_id = spec.get("dataset_id")
            if not dataset_id or dataset_id.startswith("UNRESOLVED"):
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

        else:
            unresolved_count += 1
            print(f"[{key}] {name}: Stub check (listing/probe required)")

    print(
        f"Summary: checked {checked_count}/{total_sources}, unchanged {unchanged_count}, changed {changed_count}, errors {error_count}, unresolved {unresolved_count}, blocked {blocked_count}"
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

        if kind == "datamall_geospatial_listing":
            keyword = spec.get("search_keyword", "")
            current_entry = manifest_sources.get(key, {})
            try:
                url = resolve_datamall_geospatial_url(keyword)
            except Exception as e:
                print(f"[{key}] Error discovering url for {name}: {e}")
                continue

            try:
                headers = get_headers()
                if current_entry.get("etag"):
                    headers["If-None-Match"] = current_entry["etag"]
                if current_entry.get("last_modified"):
                    headers["If-Modified-Since"] = current_entry["last_modified"]

                with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                    resp = client.get(url, headers=headers)
                    if resp.status_code == 304:
                        print(f"[{key}] {name}: unchanged (304 Not Modified), skipping ingest.")
                        continue
                    resp.raise_for_status()
                    content = resp.content

                    sha256 = hashlib.sha256(content).hexdigest()
                    etag = resp.headers.get("ETag", "")
                    last_modified = resp.headers.get("Last-Modified", "")

                    target_dir = RAW_DIR / sha256
                    target_dir.mkdir(parents=True, exist_ok=True)
                    filename = f"{key}.zip"
                    target_path = target_dir / filename

                    with open(target_path, "wb") as f:
                        f.write(content)

                    manifest_sources[key] = {
                        "source_name": name,
                        "url_as_discovered": url,
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

        elif kind == "datagov_polldownload":
            dataset_id = spec.get("dataset_id")
            if not dataset_id or dataset_id.startswith("UNRESOLVED"):
                continue

            current_entry = manifest_sources.get(key, {})
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
                        print(f"[{key}] {name}: unchanged (304 Not Modified), skipping ingest.")
                        continue

                    resp.raise_for_status()
                    content = resp.content

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

        elif kind == "osm_pbf":
            url = spec.get("url")
            if not url:
                continue

            refresh = spec.get("refresh", "auto")
            if refresh == "manual" and key in manifest_sources:
                print(f"[{key}] {name}: unchanged (refresh: manual), skipping ingest.")
                continue

            max_bytes = spec.get("max_bytes")
            if max_bytes == "2GB":
                limit = 2 * 1024 * 1024 * 1024
            else:
                limit = MAX_SIZE_BYTES

            try:
                with httpx.Client(timeout=300.0, follow_redirects=True) as client:
                    print(f"[{key}] Downloading {name} ({url}) ...")
                    resp = client.get(url, headers=get_headers())
                    resp.raise_for_status()
                    content = resp.content
                    if len(content) > limit:
                        print(f"[{key}] Error: downloaded file exceeds max_bytes")
                        continue

                    sha256 = hashlib.sha256(content).hexdigest()
                    last_modified = resp.headers.get("Last-Modified", "")
                    target_dir = RAW_DIR / sha256
                    target_dir.mkdir(parents=True, exist_ok=True)
                    filename = f"{key}.osm.pbf"
                    target_path = target_dir / filename

                    with open(target_path, "wb") as f:
                        f.write(content)

                    manifest_sources[key] = {
                        "source_name": name,
                        "url_as_discovered": url,
                        "sha256": sha256,
                        "bytes": len(content),
                        "last_modified": last_modified,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                    print(
                        f"[{key}] Ingested {name} -> raw/{sha256[:8]}.../{filename} ({len(content)} bytes)"
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
