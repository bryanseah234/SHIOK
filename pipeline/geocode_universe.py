"""Bounded OneMap geocode fill for source-derived postal-universe gaps."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pandas as pd
import yaml

from pipeline.postal_universe import lat_lon_to_xy, normalize_postal_code

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "raw"
PROCESSED_DIR = PROJECT_ROOT / "processed"
PARAMS_PATH = PROJECT_ROOT / "pipeline" / "config" / "params.yaml"
GEOCODE_DB_PATH = RAW_DIR / "geocode_cache.db"
SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search"
USER_AGENT = "SHIOK-Index-Pipeline/1.0 (Singapore Walk-to-Transit Index)"
GEOCODE_SOURCE_KEY = "onemap_search_bounded_geocode"
DEFAULT_DELAY_SEC = 2.0

FetchOneMap = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class GeocodeSelection:
    status: str
    lat: float | None = None
    lon: float | None = None
    result: dict[str, Any] | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def load_delay(params_path: Path = PARAMS_PATH) -> float:
    if not params_path.is_file():
        return DEFAULT_DELAY_SEC
    with open(params_path, "r", encoding="utf-8") as f:
        params: Any = yaml.safe_load(f) or {}
    value = params.get("onemap", {}).get("client_delay_sec") if isinstance(params, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return DEFAULT_DELAY_SEC


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_geocoded.parquet")


def default_summary_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}_summary.json")


def input_summary_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_summary.json")


def init_cache(db_path: Path = GEOCODE_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS postcodes (
            postal_code TEXT PRIMARY KEY,
            status TEXT,
            lat REAL,
            lon REAL,
            response TEXT
        )
        """
    )
    conn.commit()
    return conn


def write_cache(
    conn: sqlite3.Connection,
    postal: str,
    selection: GeocodeSelection,
    response_payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO postcodes (postal_code, status, lat, lon, response)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(postal_code) DO UPDATE SET
          status=excluded.status,
          lat=excluded.lat,
          lon=excluded.lon,
          response=excluded.response
        """,
        (
            postal,
            selection.status,
            selection.lat,
            selection.lon,
            json.dumps(response_payload, ensure_ascii=False, sort_keys=True),
        ),
    )
    conn.commit()


def cached_selection(conn: sqlite3.Connection, postal: str) -> GeocodeSelection | None:
    row = conn.execute(
        "SELECT status, lat, lon, response FROM postcodes WHERE postal_code=?", (postal,)
    ).fetchone()
    if row is None:
        return None
    status, lat, lon, response_text = row
    result = None
    if isinstance(response_text, str) and response_text:
        try:
            parsed: Any = json.loads(response_text)
            if isinstance(parsed, dict):
                result = parsed
        except json.JSONDecodeError:
            result = None
    if status == "SUCCESS" and lat is not None and lon is not None:
        return GeocodeSelection("SUCCESS", float(lat), float(lon), result)
    return GeocodeSelection(str(status), None, None, result)


def exact_result_from_payload(postal: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    results = payload.get("results", [])
    if not isinstance(results, list):
        return None
    for result in results:
        if not isinstance(result, dict):
            continue
        result_postal = normalize_postal_code(result.get("POSTAL"))
        if result_postal == postal:
            return cast(dict[str, Any], result)
    return None


def selection_from_payload(postal: str, payload: dict[str, Any]) -> GeocodeSelection:
    found = payload.get("found")
    if found in {0, "0"}:
        return GeocodeSelection("NOT_FOUND")

    result = exact_result_from_payload(postal, payload)
    if result is None:
        return GeocodeSelection("NO_EXACT_POSTAL")

    try:
        lat = float(result["LATITUDE"])
        lon = float(result["LONGITUDE"])
    except (KeyError, TypeError, ValueError):
        return GeocodeSelection("ERROR", result=result)
    return GeocodeSelection("SUCCESS", lat, lon, result)


def fetch_onemap_postal(postal: str, client: httpx.Client) -> dict[str, Any]:
    response = client.get(
        SEARCH_URL,
        params={
            "searchVal": postal,
            "returnGeom": "Y",
            "getAddrDetails": "Y",
            "pageNum": "1",
        },
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    payload: Any = response.json()
    if not isinstance(payload, dict):
        raise TypeError(f"unexpected OneMap response shape for {postal}")
    return cast(dict[str, Any], payload)


def source_list(value: Any) -> list[str]:
    if value is None:
        return []
    if hasattr(value, "tolist") and not isinstance(value, str):
        value = value.tolist()
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


def apply_success_to_row(df: pd.DataFrame, index: Any, selection: GeocodeSelection) -> None:
    if selection.lat is None or selection.lon is None:
        raise ValueError("cannot apply non-coordinate geocode selection")
    x, y = lat_lon_to_xy(selection.lat, selection.lon)
    df.at[index, "lat"] = selection.lat
    df.at[index, "lon"] = selection.lon
    df.at[index, "x"] = x
    df.at[index, "y"] = y
    df.at[index, "coordinate_source"] = GEOCODE_SOURCE_KEY
    df.at[index, "status"] = "READY_TO_SCORE"
    df.at[index, "sources"] = sorted(
        set(source_list(df.at[index, "sources"])) | {GEOCODE_SOURCE_KEY}
    )
    if selection.result:
        df.at[index, "address"] = selection.result.get("ADDRESS") or df.at[index, "address"]
        df.at[index, "building"] = selection.result.get("BUILDING") or df.at[index, "building"]
        df.at[index, "road_name"] = selection.result.get("ROAD_NAME") or df.at[index, "road_name"]


def load_json_object(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload: Any = json.load(f)
    if not isinstance(payload, dict):
        raise TypeError(f"expected JSON object in {path}")
    return cast(dict[str, Any], payload)


def build_output_summary(
    input_path: Path,
    output_path: Path,
    db_path: Path,
    df: pd.DataFrame,
    report: dict[str, Any],
) -> dict[str, Any]:
    base_summary_path = input_summary_path(input_path)
    summary = load_json_object(base_summary_path) if base_summary_path.is_file() else {}
    summary["generated_at"] = datetime.now(UTC).isoformat()
    summary["source_universe"] = str(input_path)
    summary["geocoded_universe"] = str(output_path)
    summary["total_unique_postals"] = len(df)
    summary["ready_to_score"] = int((df["status"] == "READY_TO_SCORE").sum())
    summary["needs_geocode"] = int((df["status"] == "NEEDS_GEOCODE").sum())
    summary["geocode_fill"] = report

    source_stats = [
        item
        for item in summary.get("source_stats", [])
        if isinstance(item, dict) and item.get("source_key") != GEOCODE_SOURCE_KEY
    ]
    source_stats.append(
        {
            "source_key": GEOCODE_SOURCE_KEY,
            "raw_records": int(report["http_requests"] + report["cache_successes"]),
            "valid_unique_postals": int(report["filled_successes"]),
            "records_with_coordinates": int(report["filled_successes"]),
            "path": display_path(db_path),
            "sha256": sha256_file(db_path) if db_path.is_file() else None,
            "url": SEARCH_URL,
        }
    )
    summary["source_stats"] = source_stats
    return summary


def geocode_universe_gaps(
    input_path: Path,
    output_path: Path | None = None,
    summary_path: Path | None = None,
    db_path: Path = GEOCODE_DB_PATH,
    delay_sec: float | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    confirm_bounded_geocode: bool = False,
    retry_cached_failures: bool = False,
    fetcher: FetchOneMap | None = None,
) -> tuple[bool, dict[str, Any]]:
    if not input_path.is_file():
        return False, {"ok": False, "errors": [f"postal universe not found: {input_path}"]}
    if limit is not None and limit < 0:
        return False, {"ok": False, "errors": ["limit must be >= 0"]}
    if not dry_run and not confirm_bounded_geocode:
        return False, {
            "ok": False,
            "errors": ["bounded OneMap geocode fill requires --confirm-bounded-geocode"],
        }

    delay = load_delay() if delay_sec is None else float(delay_sec)
    if delay < 0:
        return False, {"ok": False, "errors": ["delay must be >= 0"]}

    output_path = output_path or default_output_path(input_path)
    summary_path = summary_path or default_summary_path(output_path)
    df = pd.read_parquet(input_path).copy()
    df["postal_code"] = df["postal_code"].astype(str).str.zfill(6)

    needs = df[df["status"] == "NEEDS_GEOCODE"].copy()
    if limit is not None:
        needs = needs.head(int(limit))

    report: dict[str, Any] = {
        "ok": True,
        "input": str(input_path),
        "output": str(output_path),
        "summary": str(summary_path),
        "cache_db": str(db_path),
        "delay_seconds": delay,
        "dry_run": dry_run,
        "retry_cached_failures": retry_cached_failures,
        "queued_postals": len(needs),
        "http_requests": 0,
        "cache_successes": 0,
        "cache_failures": 0,
        "filled_successes": 0,
        "status_counts": {},
        "errors": [],
        "will_bruteforce": False,
    }
    if dry_run or needs.empty:
        return True, report

    conn = init_cache(db_path)
    try:
        client_context = (
            httpx.Client(timeout=30.0, follow_redirects=True) if fetcher is None else None
        )
        try:
            for index, row in needs.iterrows():
                postal = str(row["postal_code"])
                cached = cached_selection(conn, postal)
                if cached is not None and cached.status == "SUCCESS":
                    apply_success_to_row(df, index, cached)
                    report["cache_successes"] += 1
                    report["filled_successes"] += 1
                    report["status_counts"]["SUCCESS"] = (
                        int(report["status_counts"].get("SUCCESS", 0)) + 1
                    )
                    continue
                if cached is not None and not retry_cached_failures:
                    report["cache_failures"] += 1
                    report["status_counts"][cached.status] = (
                        int(report["status_counts"].get(cached.status, 0)) + 1
                    )
                    continue

                payload: dict[str, Any] | None = None
                selection = GeocodeSelection("ERROR")
                for attempt in range(1, 4):
                    try:
                        if fetcher is not None:
                            payload = fetcher(postal)
                        else:
                            if client_context is None:
                                raise RuntimeError("missing OneMap HTTP client")
                            payload = fetch_onemap_postal(postal, client_context)
                        selection = selection_from_payload(postal, payload)
                        break
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 429 and attempt < 3:
                            time.sleep(max(30.0, delay * 5.0))
                            continue
                        payload = {"error": str(exc), "status_code": exc.response.status_code}
                        selection = GeocodeSelection("ERROR")
                        break
                    except (httpx.HTTPError, TypeError, ValueError) as exc:
                        payload = {"error": str(exc)}
                        selection = GeocodeSelection("ERROR")
                        break

                report["http_requests"] += 1
                write_cache(conn, postal, selection, payload or {})
                report["status_counts"][selection.status] = (
                    int(report["status_counts"].get(selection.status, 0)) + 1
                )
                if selection.status == "SUCCESS":
                    apply_success_to_row(df, index, selection)
                    report["filled_successes"] += 1
                if delay > 0:
                    time.sleep(delay)
        finally:
            if client_context is not None:
                client_context.close()
    finally:
        conn.close()

    report["ready_to_score_after"] = int((df["status"] == "READY_TO_SCORE").sum())
    report["needs_geocode_after"] = int((df["status"] == "NEEDS_GEOCODE").sum())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    summary = build_output_summary(input_path, output_path, db_path, df, report)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)

    report["output_rows"] = len(df)
    report["summary_written"] = str(summary_path)
    return True, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill source-derived NEEDS_GEOCODE universe rows using bounded OneMap search."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--db", type=Path, default=GEOCODE_DB_PATH)
    parser.add_argument("--delay-sec", type=float)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-bounded-geocode", action="store_true")
    parser.add_argument("--retry-cached-failures", action="store_true")
    args = parser.parse_args()

    ok, report = geocode_universe_gaps(
        input_path=args.input,
        output_path=args.output,
        summary_path=args.summary,
        db_path=args.db,
        delay_sec=args.delay_sec,
        limit=args.limit,
        dry_run=bool(args.dry_run),
        confirm_bounded_geocode=bool(args.confirm_bounded_geocode),
        retry_cached_failures=bool(args.retry_cached_failures),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
