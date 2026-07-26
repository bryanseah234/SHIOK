"""Probe script to test LTA DataMall authentication and geospatial listing mechanics empirically (T0.3 Section D)."""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()


def probe_datamall() -> None:
    account_key = os.getenv("LTA_DATAMALL_ACCOUNT_KEY", "")
    api_url = "https://datamall2.mytransport.sg/ltaodataservice/BusStops?$skip=0"
    static_page_url = "https://datamall.lta.gov.sg/content/datamall/en/static-data.html"

    print("Probing LTA DataMall API & Geospatial Listing mechanics...")

    # 1. API endpoint probe (Unauthenticated vs Authenticated)
    print("\n--- 1. API Endpoint Probe (BusStops) ---")
    try:
        r_unauth = httpx.get(api_url, timeout=10)
        print(f"Unauthenticated request status: {r_unauth.status_code}")
    except httpx.HTTPError as e:
        print(f"Unauthenticated request error: {e}")

    if account_key:
        headers = {"AccountKey": account_key}
        try:
            r_auth = httpx.get(api_url, headers=headers, timeout=10)
            print(f"Authenticated request status: {r_auth.status_code}")
            if r_auth.status_code == 200:
                print(
                    "Authenticated request successful! Sample value count:",
                    len(r_auth.json().get("value", [])),
                )
            elif r_auth.status_code == 401:
                print(
                    "Authenticated request returned 401 Unauthorized (AccountKey invalid or pending approval)."
                )
        except httpx.HTTPError as e:
            print(f"Authenticated request error: {e}")
    else:
        print("No LTA_DATAMALL_ACCOUNT_KEY present in .env; skipping authenticated API probe.")

    # 2. Geospatial Listing Page Probe (Rows 1-2)
    print("\n--- 2. Geospatial Listing Page Probe ---")
    try:
        r_static = httpx.get(static_page_url, follow_redirects=True, timeout=10)
        print(f"Geospatial listing page status: {r_static.status_code}, Final URL: {r_static.url}")
    except httpx.HTTPError as e:
        print(f"Geospatial listing page error: {e}")


if __name__ == "__main__":
    probe_datamall()
