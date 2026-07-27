"""Parse geospatial static dataset URLs from LTA DataMall static-data.html."""

import re
import httpx


def parse_datamall_static_links() -> None:
    url = "https://datamall.lta.gov.sg/content/datamall/en/static-data.html"
    resp = httpx.get(url, follow_redirects=True, timeout=15)
    print("Page status:", resp.status_code)

    # Search for zip file links in page HTML
    zip_links = re.findall(r'href=["\']([^"\']+\.zip)["\']', resp.text, re.IGNORECASE)
    print(f"Found {len(zip_links)} zip links:")
    for link in zip_links:
        if (
            "linkway" in link.lower()
            or "bridge" in link.lower()
            or "overhead" in link.lower()
            or "geospatial" in link.lower()
        ):
            full_url = link if link.startswith("http") else f"https://datamall.lta.gov.sg{link}"
            print(" -", full_url)


if __name__ == "__main__":
    parse_datamall_static_links()
