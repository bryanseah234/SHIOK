"""Resolve correct data.gov.sg dataset IDs for S.H.I.O.K. sources."""

import time

import httpx

SEARCH_QUERIES = [
    ("mrt_lrt_exits", "train station exit point"),
    ("traffic_signals", "traffic signal"),
    ("lamp_posts", "lamp post"),
    ("building_points", "hdb property information"),
    ("planning_area_boundary", "master plan 2019 planning area boundary"),
]

API_BASE = "https://api-open.data.gov.sg/v1/public/api/datasets"


def main() -> None:
    client = httpx.Client(timeout=30.0)

    for key, query in SEARCH_QUERIES:
        time.sleep(3.0)  # Politeness throttle
        try:
            url = f"{API_BASE}?query={query}"
            resp = client.get(url)
            if resp.status_code == 429:
                print(f"[{key}] 429 Too Many Requests, waiting 10s...")
                time.sleep(10.0)
                resp = client.get(url)

            resp.raise_for_status()
            data = resp.json()
            datasets = data.get("data", {}).get("datasets", [])

            print(f"\n--- [{key}] query='{query}' ---")
            for ds in datasets[:5]:
                name = ds.get("name", "")
                ds_id = ds.get("datasetId", "")
                print(f"  {ds_id}  {name}")

        except Exception as e:
            print(f"[{key}] Error: {e}")

    client.close()


if __name__ == "__main__":
    main()
