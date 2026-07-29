import httpx

from pipeline.fetch import datagov_raw_filename, stable_manifest_url


def test_datagov_raw_filename_uses_content_disposition_extension() -> None:
    headers = httpx.Headers(
        {
            "content-disposition": 'attachment; filename="LeafAreaIndexLAI.xlsx"',
            "content-type": "binary/octet-stream",
        }
    )

    assert datagov_raw_filename("leaf_area_index", "https://example.test/download", headers) == (
        "leaf_area_index.xlsx"
    )


def test_datagov_raw_filename_uses_url_extension() -> None:
    headers = httpx.Headers({"content-type": "binary/octet-stream"})

    assert datagov_raw_filename(
        "mrt_lrt_exits", "https://example.test/source.geojson?sig=1", headers
    ) == ("mrt_lrt_exits.geojson")


def test_datagov_raw_filename_falls_back_to_content_type() -> None:
    headers = httpx.Headers({"content-type": "text/csv; charset=utf-8"})

    assert datagov_raw_filename("sample", "https://example.test/download", headers) == "sample.csv"


def test_stable_manifest_url_strips_signed_s3_query() -> None:
    url = (
        "https://s3.ap-southeast-1.amazonaws.com/blobs.data.gov.sg/source.xlsx?"
        "AWSAccessKeyId=example&Expires=1785330614&Signature=abc"
    )

    assert stable_manifest_url(url) == (
        "https://s3.ap-southeast-1.amazonaws.com/blobs.data.gov.sg/source.xlsx"
    )


def test_stable_manifest_url_preserves_normal_query() -> None:
    url = "https://example.test/api?searchVal=560234&returnGeom=Y"

    assert stable_manifest_url(url) == url
