"""Resolve dataset title and ID by searching data.gov.sg dataset pages."""

import re
import httpx


def resolve_dataset_by_keyword(query: str) -> tuple[str, str] | None:
    url = f"https://data.gov.sg/datasets?query={query}"
    headers = {"User-Agent": "SHIOK-Index-Pipeline/1.0"}
    try:
        resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=10)
        if resp.status_code == 200:
            # Match dataset ID from links or page text
            ids = re.findall(r"d_[a-f0-9]{32}", resp.text)
            if ids:
                dataset_id = ids[0]
                # Test initiate-download
                dl_url = f"https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/initiate-download"
                dl_resp = httpx.get(dl_url, headers=headers, timeout=10)
                if dl_resp.status_code == 201:
                    filename = (
                        dl_resp.json()
                        .get("data", {})
                        .get("url", "")
                        .split("filename%3D%22")[-1]
                        .split("%22")[0]
                    )
                    title = filename.replace(".geojson", "").replace(".zip", "").replace("%20", " ")
                    print(f"[{query}] Resolved ID: {dataset_id}, Title: {title}")
                    return dataset_id, title
    except Exception as e:
        print(f"[{query}] Search error: {e}")
    return None


if __name__ == "__main__":
    for q in ["MRT", "Traffic Signal", "Lamp Post", "HDB"]:
        resolve_dataset_by_keyword(q)
