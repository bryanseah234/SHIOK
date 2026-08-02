"""Local LTA DataMall bus-arrival snapshot collector.

This is for future historical reliability scoring. It writes local JSONL
snapshots only; production remains static-first.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from pipeline.bus import datamall_headers

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "raw" / "bus_arrivals" / "arrivals.jsonl"
BUS_ARRIVAL_ENDPOINT = "https://datamall2.mytransport.sg/ltaodataservice/v3/BusArrival"


def snapshot_record(
    *,
    bus_stop_code: str,
    payload: dict[str, Any],
    service_no: str | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    return {
        "fetched_at": fetched_at or datetime.now(UTC).isoformat(),
        "source": "lta_datamall_bus_arrival_v3",
        "bus_stop_code": bus_stop_code,
        "service_no": service_no,
        "payload": payload,
    }


def fetch_bus_arrival(
    bus_stop_code: str,
    service_no: str | None = None,
    endpoint: str = BUS_ARRIVAL_ENDPOINT,
) -> dict[str, Any]:
    params = {"BusStopCode": bus_stop_code}
    if service_no:
        params["ServiceNo"] = service_no
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        response = client.get(endpoint, headers=datamall_headers(), params=params)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("Bus arrival payload must be a JSON object")
    return payload


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            f.write("\n")
    return len(records)


def collect_snapshots(
    *,
    stops: list[str],
    output: Path = DEFAULT_OUTPUT,
    samples: int = 1,
    interval_sec: float = 60.0,
    service_no: str | None = None,
) -> dict[str, Any]:
    if not stops:
        raise ValueError("at least one bus stop code is required")
    if samples < 1:
        raise ValueError("samples must be >= 1")
    if interval_sec < 0:
        raise ValueError("interval_sec must be >= 0")

    written = 0
    for sample_index in range(samples):
        records = []
        fetched_at = datetime.now(UTC).isoformat()
        for stop in stops:
            payload = fetch_bus_arrival(stop, service_no=service_no)
            records.append(
                snapshot_record(
                    bus_stop_code=stop,
                    service_no=service_no,
                    payload=payload,
                    fetched_at=fetched_at,
                )
            )
        written += append_jsonl(output, records)
        if sample_index < samples - 1:
            time.sleep(interval_sec)

    return {
        "ok": True,
        "output": str(output),
        "stops": stops,
        "samples": samples,
        "records_written": written,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect local bus-arrival snapshots.")
    parser.add_argument("action", choices=["collect"])
    parser.add_argument("--stop", action="append", dest="stops", required=True)
    parser.add_argument("--service")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval-sec", type=float, default=60.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.action == "collect":
        report = collect_snapshots(
            stops=[str(stop).strip() for stop in args.stops if str(stop).strip()],
            output=args.output,
            samples=args.samples,
            interval_sec=args.interval_sec,
            service_no=args.service,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
