"""One-off local probe to test OneMap search API rate limits empirically (T0.4)."""

import time
import httpx


def probe_onemap_rate_limit() -> None:
    url = "https://www.onemap.gov.sg/api/common/elastic/search?searchVal=Toa%20Payoh&returnGeom=Y&getAddrDetails=Y"
    headers = {"User-Agent": "SHIOK-Index-Probe/1.0"}

    print("Probing OneMap search API rate limits (controlled ramp)...")
    client = httpx.Client(timeout=10.0)

    request_count = 0
    hit_429 = False
    retry_after = None

    try:
        # Ramp requests
        for i in range(1, 61):
            resp = client.get(url, headers=headers)
            request_count += 1
            if resp.status_code == 429:
                hit_429 = True
                retry_after = resp.headers.get("Retry-After")
                print(
                    f"Hit 429 Too Many Requests at request #{request_count}. Retry-After: {retry_after}"
                )
                break
            elif resp.status_code != 200:
                print(f"Request #{request_count} returned status {resp.status_code}")
            time.sleep(0.05)  # 20 req/sec ramp rate
    except httpx.HTTPError as e:
        print(f"HTTP error during probe: {e}")
    finally:
        client.close()

    if not hit_429:
        print(f"Completed {request_count} requests without hitting 429 rate limit.")


if __name__ == "__main__":
    probe_onemap_rate_limit()
