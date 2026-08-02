import json

from pipeline.bus_arrivals import append_jsonl, collect_snapshots, snapshot_record


def test_snapshot_record_is_honest_local_collection_payload():
    payload = {"Services": [{"ServiceNo": "169"}]}

    record = snapshot_record(
        bus_stop_code="54211",
        service_no="169",
        payload=payload,
        fetched_at="2026-07-29T12:00:00+00:00",
    )

    assert record == {
        "fetched_at": "2026-07-29T12:00:00+00:00",
        "source": "lta_datamall_bus_arrival_v3",
        "bus_stop_code": "54211",
        "service_no": "169",
        "payload": payload,
    }


def test_append_jsonl_writes_one_record_per_line(tmp_path):
    path = tmp_path / "arrivals.jsonl"
    records = [
        snapshot_record(bus_stop_code="54211", payload={"Services": []}, fetched_at="t1"),
        snapshot_record(bus_stop_code="54221", payload={"Services": []}, fetched_at="t2"),
    ]

    written = append_jsonl(path, records)

    assert written == 2
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["bus_stop_code"] for line in lines] == ["54211", "54221"]


def test_collect_snapshots_uses_injected_fetcher(monkeypatch, tmp_path):
    from pipeline import bus_arrivals

    def fake_fetch(stop, service_no=None):
        return {"Services": [{"BusStopCode": stop, "ServiceNo": service_no}]}

    monkeypatch.setattr(bus_arrivals, "fetch_bus_arrival", fake_fetch)
    output = tmp_path / "arrivals.jsonl"

    report = collect_snapshots(
        stops=["54211", "54221"],
        output=output,
        samples=1,
        interval_sec=0,
        service_no="169",
    )

    assert report["ok"] is True
    assert report["records_written"] == 2
    assert output.read_text(encoding="utf-8").count("\n") == 2
