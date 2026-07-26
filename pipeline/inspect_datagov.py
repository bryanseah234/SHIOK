"""Search for api-production.data.gov.sg routes in JS files."""

import re
import httpx


def search_api_routes() -> None:
    url = "https://data.gov.sg/datasets?query=MRT"
    resp = httpx.get(url, follow_redirects=True, timeout=10)
    scripts = re.findall(r'src="(/_next/static/[^"]+)"', resp.text)

    for s in scripts:
        s_url = f"https://data.gov.sg{s}"
        try:
            r = httpx.get(s_url, timeout=5)
            if "api-production.data.gov.sg" in r.text or "datasets" in r.text:
                routes = re.findall(
                    r"https://api-production\.data\.gov\.sg/[a-zA-Z0-9_/.-]+", r.text
                )
                if routes:
                    print(f"Script {s[-20:]} routes:", set(routes))
        except Exception:
            pass


if __name__ == "__main__":
    search_api_routes()
