"""Probe script to test LTA DataMall authentication mechanics empirically."""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()


def probe_datamall() -> None:
    account_key = os.getenv("LTA_DATAMALL_ACCOUNT_KEY", "")
    url = "http://datamall2.mytransport.sg/ltaodataservice/BusStops"

    print("Probing DataMall API mechanics...")

    # 1. Try unauthenticated
    try:
        resp = httpx.get(url, timeout=10)
        print(f"Unauthenticated status code: {resp.status_code}")
    except httpx.HTTPError as e:
        print(f"Unauthenticated request error: {e}")

    # 2. Try authenticated with AccountKey header (redacted)
    if account_key:
        headers = {"AccountKey": account_key}
        try:
            resp_auth = httpx.get(url, headers=headers, timeout=10)
            print(f"Authenticated status code: {resp_auth.status_code}")
        except httpx.HTTPError as e:
            print(f"Authenticated request error: {e}")
    else:
        print("No LTA_DATAMALL_ACCOUNT_KEY found in .env; skipping authenticated probe.")


if __name__ == "__main__":
    probe_datamall()
