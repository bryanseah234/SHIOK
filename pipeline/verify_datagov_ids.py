"""Verify correct data.gov.sg dataset IDs via initiate-download API."""

import time
import httpx

DATASETS = {
    "mrt_lrt_exits": "d_b39d3a0871985372d7e1637193335da5",
    "traffic_signals": "d_f40071375d045d94726e2570075d5069",
    "lamp_posts": "d_ca109de3e83efdd9a10bc5f3dda70a98",
    "building_points": "d_16b157c52ed637edd6ba1232e026258d",
    "planning_area_boundary": "d_4765db0e87b9c86336792efe8a1f7a66",
}


def main() -> None:
    client = httpx.Client(timeout=30.0)

    for key, dataset_id in DATASETS.items():
        time.sleep(3.0)
        url = f"https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/initiate-download"
        try:
            resp = client.get(url)
            if resp.status_code == 429:
                print(f"[{key}] 429 Too Many Requests, retrying in 10s...")
                time.sleep(10.0)
                resp = client.get(url)

            data = resp.json()
            download_url = data.get("data", {}).get("url", "")
            message = data.get("data", {}).get("message", "")
            print(f"[{key}] {dataset_id}: status={resp.status_code}")
            if download_url:
                print(f"  download_url={download_url[:120]}")
            if message:
                print(f"  message={message}")
        except Exception as e:
            print(f"[{key}] Error: {e}")

    client.close()


if __name__ == "__main__":
    main()
