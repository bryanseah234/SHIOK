from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "processed"
RAW_DIR = PROJECT_ROOT / "raw"
DEFAULT_CURRENT_UNIVERSE = (
    PROCESSED_DIR / "postal_universe_candidate_full_registered_geocoded.parquet"
)
DEFAULT_OVERTURE_PATH = (
    "s3://overturemaps-us-west-2/release/2026-07-22.0/theme=addresses/type=address/*"
)
POSTCODE_RE = re.compile(r"^[0-9]{6}$")


def normalize_postcode(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not POSTCODE_RE.fullmatch(text):
        return None
    return text


def current_universe_postcodes(path: Path) -> set[str]:
    table = pq.read_table(path, columns=["postal_code"])
    postcodes: set[str] = set()
    for value in table.column("postal_code").to_pylist():
        postcode = normalize_postcode(value)
        if postcode is not None:
            postcodes.add(postcode)
    return postcodes


def compare_postcode_sets(
    overture_postcodes: set[str],
    current_postcodes: set[str],
) -> dict[str, Any]:
    new_from_overture = sorted(overture_postcodes - current_postcodes)
    current_missing_from_overture = sorted(current_postcodes - overture_postcodes)
    return {
        "overture_unique_postcodes": len(overture_postcodes),
        "current_unique_postcodes": len(current_postcodes),
        "intersection": len(overture_postcodes & current_postcodes),
        "new_from_overture": len(new_from_overture),
        "current_missing_from_overture": len(current_missing_from_overture),
        "sample_new_from_overture": new_from_overture[:20],
        "sample_current_missing_from_overture": current_missing_from_overture[:20],
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_overture_postcode_rows(
    rows: list[dict[str, Any]],
    *,
    raw_dir: Path = RAW_DIR,
) -> dict[str, Any]:
    table = pa.table(
        {
            "postcode": [row["postcode"] for row in rows],
            "address_rows": [int(row["address_rows"]) for row in rows],
            "source_dataset": [row.get("source_dataset") for row in rows],
            "min_lon": [row.get("min_lon") for row in rows],
            "min_lat": [row.get("min_lat") for row in rows],
            "max_lon": [row.get("max_lon") for row in rows],
            "max_lat": [row.get("max_lat") for row in rows],
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / "overture_addresses_sg_postcodes.parquet"
        pq.write_table(table, tmp_path)
        digest = sha256_file(tmp_path)
        target_dir = raw_dir / digest
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / tmp_path.name
        shutil.copy2(tmp_path, target_path)
    return {
        "path": str(target_path),
        "sha256": digest,
        "rows": len(rows),
    }


def query_overture_singapore_postcodes(overture_path: str) -> dict[str, Any]:
    con = duckdb.connect()
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute("SET s3_region='us-west-2'")

    stats = con.execute(
        f"""
        SELECT
          count(*) AS rows,
          count(*) FILTER (WHERE postcode IS NULL OR postcode = '') AS missing_postcode_rows,
          min(bbox.xmin) AS min_lon,
          min(bbox.ymin) AS min_lat,
          max(bbox.xmax) AS max_lon,
          max(bbox.ymax) AS max_lat
        FROM read_parquet('{overture_path}', filename=true, hive_partitioning=1)
        WHERE country = 'SG'
        """
    ).fetchone()

    rows = [
        {
            "postcode": str(row[0]),
            "address_rows": int(row[1]),
            "source_dataset": row[2],
            "min_lon": row[3],
            "min_lat": row[4],
            "max_lon": row[5],
            "max_lat": row[6],
        }
        for row in con.execute(
            f"""
            SELECT
              postcode,
              count(*) AS address_rows,
              any_value(sources[1].dataset) AS source_dataset,
              min(bbox.xmin) AS min_lon,
              min(bbox.ymin) AS min_lat,
              max(bbox.xmax) AS max_lon,
              max(bbox.ymax) AS max_lat
            FROM read_parquet('{overture_path}', filename=true, hive_partitioning=1)
            WHERE country = 'SG' AND regexp_matches(postcode, '^[0-9]{{6}}$')
            GROUP BY postcode
            ORDER BY postcode
            """
        ).fetchall()
    ]

    source_counts = Counter(str(row.get("source_dataset")) for row in rows)
    return {
        "release_path": overture_path,
        "rows": int(stats[0]),
        "unique_six_digit_postcodes": len(rows),
        "missing_postcode_rows": int(stats[1]),
        "bbox": {
            "min_lon": stats[2],
            "min_lat": stats[3],
            "max_lon": stats[4],
            "max_lat": stats[5],
        },
        "source_dataset_counts": dict(source_counts.most_common(10)),
        "postcode_rows": rows,
    }


def build_overture_candidate_report(
    *,
    current_universe_path: Path = DEFAULT_CURRENT_UNIVERSE,
    overture_path: str = DEFAULT_OVERTURE_PATH,
    archive_raw: bool = False,
    raw_dir: Path = RAW_DIR,
) -> tuple[bool, dict[str, Any]]:
    errors: list[str] = []
    if not current_universe_path.is_file():
        errors.append(f"missing current universe parquet: {current_universe_path}")
        return False, {"ok": False, "errors": errors}

    current = current_universe_postcodes(current_universe_path)
    overture = query_overture_singapore_postcodes(overture_path)
    overture_postcodes = {str(row["postcode"]) for row in overture["postcode_rows"]}
    comparison = compare_postcode_sets(overture_postcodes, current)
    raw_archive = None
    if archive_raw:
        raw_archive = archive_overture_postcode_rows(overture["postcode_rows"], raw_dir=raw_dir)

    report: dict[str, Any] = {
        "ok": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "name": "Overture Maps Addresses — Singapore candidate",
            "status": "candidate_not_scoring",
            "theme_status": "Alpha",
            "release_path": overture_path,
            "country": "SG",
            "production_decision": (
                "candidate only until raw archive, hash/provenance, dedupe, "
                "coordinate validation, and attribution review pass"
            ),
        },
        "current_universe": {
            "path": str(current_universe_path),
            "unique_postcodes": len(current),
        },
        "overture": {key: value for key, value in overture.items() if key != "postcode_rows"},
        "comparison": comparison,
        "raw_archive": raw_archive,
        "errors": errors,
    }
    return True, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe Overture Addresses SG as a postal-universe candidate."
    )
    parser.add_argument("--current-universe", type=Path, default=DEFAULT_CURRENT_UNIVERSE)
    parser.add_argument("--overture-path", default=DEFAULT_OVERTURE_PATH)
    parser.add_argument("--archive-raw", action="store_true")
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    ok, report = build_overture_candidate_report(
        current_universe_path=args.current_universe,
        overture_path=args.overture_path,
        archive_raw=bool(args.archive_raw),
        raw_dir=args.raw_dir,
    )
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
