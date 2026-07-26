"""Sustained-rate ladder probe for OneMap search API rate limits (T0.4 Audit Remediation B)."""

import csv
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
CSV_PATH = LOG_DIR / "onemap_probe.csv"

SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search?searchVal=Toa%20Payoh&returnGeom=Y&getAddrDetails=Y"
HEADERS = {"User-Agent": "SHIOK-Index-Probe/2.0"}


def run_ladder_probe() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    ladder_rungs = [
        {"rung": 1, "target_rate_rps": 1, "num_requests": 60},
        {"rung": 2, "target_rate_rps": 2, "num_requests": 60},
        {"rung": 3, "target_rate_rps": 4, "num_requests": 120},
        {"rung": 4, "target_rate_rps": 5, "num_requests": 60},
    ]

    print("Starting OneMap sustained-rate ladder probe...")
    print(f"Target URL: {SEARCH_URL}")

    client = httpx.Client(timeout=10.0)
    total_requests = 0
    first_429_info = None
    highest_clean_rate_rps = 0

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(
            ["timestamp", "request_num", "rung", "req_per_sec", "status_code", "elapsed_sec"]
        )

        try:
            for rung_info in ladder_rungs:
                rung_num = rung_info["rung"]
                target_rps = rung_info["target_rate_rps"]
                n_reqs = rung_info["num_requests"]
                interval = 1.0 / target_rps

                print(f"\n--- Rung {rung_num}: {n_reqs} requests at {target_rps} req/sec ---")
                rung_had_error = False

                for _ in range(n_reqs):
                    t_start = time.perf_counter()
                    timestamp_str = datetime.now(timezone.utc).isoformat()
                    total_requests += 1

                    resp = client.get(SEARCH_URL, headers=HEADERS)
                    t_elapsed = time.perf_counter() - t_start

                    writer.writerow(
                        [
                            timestamp_str,
                            total_requests,
                            rung_num,
                            target_rps,
                            resp.status_code,
                            f"{t_elapsed:.4f}",
                        ]
                    )
                    csvfile.flush()

                    if resp.status_code == 429:
                        rung_had_error = True
                        retry_after = resp.headers.get("Retry-After")
                        body_snippet = resp.text[:200]
                        first_429_info = {
                            "request_num": total_requests,
                            "rung": rung_num,
                            "target_rps": target_rps,
                            "retry_after": retry_after,
                            "body_snippet": body_snippet,
                        }
                        print(
                            f"STOPPING: Hit 429 Too Many Requests at request #{total_requests} (Rung {rung_num}, {target_rps} req/s). Retry-After: {retry_after}"
                        )
                        break
                    elif resp.status_code != 200:
                        rung_had_error = True
                        print(
                            f"Request #{total_requests} returned non-200 status: {resp.status_code}"
                        )
                        break

                    # Sleep to enforce exact rate
                    sleep_time = interval - t_elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)

                if rung_had_error:
                    break
                else:
                    highest_clean_rate_rps = target_rps
                    print(
                        f"Rung {rung_num} CLEAN: Sustained {target_rps} req/sec for {n_reqs} requests."
                    )

        finally:
            client.close()

    print("\n================ Probe Summary ================")
    print(f"Total requests executed: {total_requests}")
    print(f"Highest sustained clean rate (R): {highest_clean_rate_rps} req/sec")

    if first_429_info:
        print(
            f"First 429 Context: Request #{first_429_info['request_num']} on Rung {first_429_info['rung']} ({first_429_info['target_rps']} req/s)"
        )
        print(f"Retry-After header: {first_429_info['retry_after']}")
        print(f"Response Body: {first_429_info['body_snippet']}")
    else:
        print("No 429 status code encountered during ladder probe.")


if __name__ == "__main__":
    run_ladder_probe()
