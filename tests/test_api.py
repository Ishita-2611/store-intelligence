# PROMPT: Generate FastAPI tests for a Store Intelligence API that ingests event batches idempotently and computes metrics, funnel, heatmap, anomalies, and health from session events.
# CHANGES MADE: Replaced generic fixtures with edge-case events that cover staff exclusion, duplicate ingest, billing abandonment, and low-confidence-but-valid events.

import json
import time

from fastapi.testclient import TestClient

from app import replay
from app.ingestion import store
from app.main import app
from app.uploads import UploadJob, upload_controller


client = TestClient(app)


def setup_function() -> None:
    replay.replay_controller.stop()
    upload_controller.reset()
    store.reset()


def event(
    event_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: str,
    zone_id: str | None = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    queue_depth: int | None = None,
) -> dict:
    return {
        "event_id": event_id,
        "store_id": "ST1076",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": 0.72,
        "metadata": {"queue_depth": queue_depth, "sku_zone": zone_id, "session_seq": 1},
    }


def seed_events() -> list[dict]:
    return [
        event("e1", "VIS_1", "ENTRY", "2026-04-10T10:00:00Z"),
        event("e2", "VIS_1", "ZONE_ENTER", "2026-04-10T10:01:00Z", "PURPLLE_MUM_1076_Z01"),
        event("e3", "VIS_1", "ZONE_EXIT", "2026-04-10T10:03:00Z", "PURPLLE_MUM_1076_Z01", dwell_ms=120000),
        event("e4", "VIS_1", "BILLING_QUEUE_JOIN", "2026-04-10T10:05:00Z", "PURPLLE_MUM_1076_Z_BILLING_01", queue_depth=1),
        event("e5", "VIS_2", "ENTRY", "2026-04-10T10:10:00Z"),
        event("e6", "VIS_2", "ZONE_ENTER", "2026-04-10T10:12:00Z", "PURPLLE_MUM_1076_Z02"),
        event("e7", "VIS_2", "BILLING_QUEUE_JOIN", "2026-04-10T10:20:00Z", "PURPLLE_MUM_1076_Z_BILLING_01", queue_depth=2),
        event("e8", "VIS_2", "BILLING_QUEUE_ABANDON", "2026-04-10T10:25:00Z", "PURPLLE_MUM_1076_Z_BILLING_01", dwell_ms=300000, queue_depth=1),
        event("e9", "VIS_STAFF", "ZONE_ENTER", "2026-04-10T10:30:00Z", "STAFF_ONLY", is_staff=True),
    ]


def test_ingest_is_idempotent_and_partial_success() -> None:
    events = seed_events()
    response = client.post("/events/ingest", json={"events": events + [{"event_id": ""}]})

    assert response.status_code == 200
    assert response.json()["accepted"] == len(events)
    assert response.json()["rejected"] == 1

    duplicate = client.post("/events/ingest", json={"events": events})

    assert duplicate.status_code == 200
    assert duplicate.json()["accepted"] == 0
    assert duplicate.json()["duplicates"] == len(events)


def test_metrics_exclude_staff_and_compute_conversion() -> None:
    client.post("/events/ingest", json={"events": seed_events()})

    metrics = client.get("/stores/ST1076/metrics").json()
    default_metrics = client.get("/Metrics").json()

    assert metrics["unique_visitors"] == 2
    assert default_metrics["unique_visitors"] == 2
    assert metrics["converted_visitors"] == 1
    assert metrics["conversion_rate"] == 0.5
    assert metrics["abandonment_rate"] == 0.5
    assert metrics["avg_dwell_ms_per_zone"]["PURPLLE_MUM_1076_Z01"] == 120000


def test_funnel_heatmap_anomalies_and_health() -> None:
    client.post("/events/ingest", json={"events": seed_events()})

    funnel = client.get("/stores/ST1076/funnel").json()
    heatmap = client.get("/stores/ST1076/heatmap").json()
    anomalies = client.get("/stores/ST1076/anomalies").json()
    health = client.get("/health").json()

    assert [stage["count"] for stage in funnel["stages"]] == [2, 2, 2, 1]
    assert heatmap["data_confidence"] == "LOW"
    assert any(zone["zone_id"] == "PURPLLE_MUM_1076_Z01" for zone in heatmap["zones"])
    assert isinstance(anomalies["anomalies"], list)
    assert health["stores"]["ST1076"]["warning"] == "STALE_FEED"


def test_zero_purchase_and_all_staff_edge_cases() -> None:
    all_staff = [
        event("staff-1", "VIS_STAFF", "ZONE_ENTER", "2026-04-10T11:00:00Z", "STAFF_ONLY", is_staff=True),
        event("staff-2", "VIS_STAFF", "ZONE_EXIT", "2026-04-10T11:02:00Z", "STAFF_ONLY", dwell_ms=120000, is_staff=True),
    ]
    client.post("/events/ingest", json={"events": all_staff})

    metrics = client.get("/stores/ST1076/metrics").json()
    funnel = client.get("/stores/ST1076/funnel").json()

    assert metrics["unique_visitors"] == 0
    assert metrics["conversion_rate"] == 0.0
    assert metrics["event_count"] == 0
    assert [stage["count"] for stage in funnel["stages"]] == [0, 0, 0, 0]


def test_reentry_does_not_double_count_session() -> None:
    reentry_events = [
        event("r1", "VIS_1", "ENTRY", "2026-04-10T12:00:00Z"),
        event("r2", "VIS_1", "EXIT", "2026-04-10T12:10:00Z"),
        event("r3", "VIS_1", "REENTRY", "2026-04-10T12:15:00Z"),
        event("r4", "VIS_1", "ZONE_ENTER", "2026-04-10T12:16:00Z", "PURPLLE_MUM_1076_Z01"),
    ]
    client.post("/events/ingest", json={"events": reentry_events})

    metrics = client.get("/stores/ST1076/metrics").json()
    funnel = client.get("/stores/ST1076/funnel").json()

    assert metrics["unique_visitors"] == 1
    assert funnel["stages"][0]["count"] == 1


def test_store_unavailable_returns_structured_503() -> None:
    store.set_available(False)

    response = client.get("/stores/ST1076/metrics")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORE_UNAVAILABLE"


def test_request_logging_includes_trace_and_event_count(caplog) -> None:
    caplog.set_level("INFO", logger="store_intelligence")

    response = client.post("/events/ingest", headers={"x-trace-id": "trace-test"}, json={"events": [seed_events()[0]]})

    assert response.status_code == 200
    assert response.headers["x-trace-id"] == "trace-test"
    assert '"trace_id":"trace-test"' in caplog.text
    assert '"event_count":1' in caplog.text


def test_dashboard_and_replay_status_are_available() -> None:
    dashboard = client.get("/dashboard")
    status = client.get("/demo/replay/status")

    assert dashboard.status_code == 200
    assert "Store Intelligence Command Center" in dashboard.text
    assert status.status_code == 200
    assert status.json()["running"] is False


def test_demo_replay_streams_events_from_jsonl(monkeypatch, tmp_path) -> None:
    demo_path = tmp_path / "events.jsonl"
    demo_events = [
        event("demo-1", "VIS_DEMO", "ENTRY", "2026-04-10T13:00:00Z"),
        event("demo-2", "VIS_DEMO", "ZONE_ENTER", "2026-04-10T13:01:00Z", "PURPLLE_MUM_1076_Z01"),
    ]
    demo_path.write_text("\n".join(json.dumps(item) for item in demo_events), encoding="utf-8")
    monkeypatch.setattr(replay, "DEMO_EVENTS_PATH", demo_path)

    client.post("/demo/replay/reset")
    response = client.post("/demo/replay/start?batch_size=1&interval_ms=100")

    assert response.status_code == 200
    for _ in range(20):
        status = client.get("/demo/replay/status").json()
        if not status["running"]:
            break
        time.sleep(0.05)

    status = client.get("/demo/replay/status").json()
    metrics = client.get("/stores/ST1076/metrics").json()

    assert status["ingested_events"] == 2
    assert metrics["unique_visitors"] == 1


def test_reset_stops_running_sample_replay(monkeypatch, tmp_path) -> None:
    demo_path = tmp_path / "events.jsonl"
    demo_events = [
        event("reset-1", "VIS_RESET", "ENTRY", "2026-04-10T13:00:00Z"),
        event("reset-2", "VIS_RESET", "ZONE_ENTER", "2026-04-10T13:01:00Z", "PURPLLE_MUM_1076_Z01"),
    ]
    demo_path.write_text("\n".join(json.dumps(item) for item in demo_events), encoding="utf-8")
    monkeypatch.setattr(replay, "DEMO_EVENTS_PATH", demo_path)

    client.post("/demo/replay/start?batch_size=1&interval_ms=5000")
    response = client.post("/demo/replay/reset")

    assert response.status_code == 200
    assert response.json()["running"] is False
    assert client.get("/stores/ST1076/metrics").json()["event_count"] == 0


def test_upload_start_stops_sample_replay(monkeypatch, tmp_path) -> None:
    demo_path = tmp_path / "events.jsonl"
    demo_path.write_text(json.dumps(event("upload-stop-1", "VIS_DEMO", "ENTRY", "2026-04-10T13:00:00Z")), encoding="utf-8")
    monkeypatch.setattr(replay, "DEMO_EVENTS_PATH", demo_path)

    def fake_create_job(upload) -> UploadJob:
        return UploadJob(job_id="upload123", filename=upload.filename, status="queued")

    client.post("/demo/replay/start?batch_size=1&interval_ms=5000")
    assert client.get("/stores/ST1076/metrics").json()["event_count"] == 1
    monkeypatch.setattr(upload_controller, "create_job", fake_create_job)
    response = client.post("/uploads/cctv", files={"file": ("new-footage.mp4", b"video", "video/mp4")})

    assert response.status_code == 200
    assert response.json()["filename"] == "new-footage.mp4"
    assert client.get("/demo/replay/status").json()["running"] is False
    assert client.get("/stores/ST1076/metrics").json()["event_count"] == 0
