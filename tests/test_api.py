# PROMPT: Generate FastAPI tests for a Store Intelligence API that ingests event batches idempotently and computes metrics, funnel, heatmap, anomalies, and health from session events.
# CHANGES MADE: Replaced generic fixtures with edge-case events that cover staff exclusion, duplicate ingest, billing abandonment, and low-confidence-but-valid events.

from fastapi.testclient import TestClient

from app.ingestion import store
from app.main import app


client = TestClient(app)


def setup_function() -> None:
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
        "store_id": "ST1008",
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
        event("e2", "VIS_1", "ZONE_ENTER", "2026-04-10T10:01:00Z", "SKINCARE"),
        event("e3", "VIS_1", "ZONE_EXIT", "2026-04-10T10:03:00Z", "SKINCARE", dwell_ms=120000),
        event("e4", "VIS_1", "BILLING_QUEUE_JOIN", "2026-04-10T10:05:00Z", "BILLING_COUNTER", queue_depth=1),
        event("e5", "VIS_2", "ENTRY", "2026-04-10T10:10:00Z"),
        event("e6", "VIS_2", "ZONE_ENTER", "2026-04-10T10:12:00Z", "MAKEUP"),
        event("e7", "VIS_2", "BILLING_QUEUE_JOIN", "2026-04-10T10:20:00Z", "BILLING_COUNTER", queue_depth=2),
        event("e8", "VIS_2", "BILLING_QUEUE_ABANDON", "2026-04-10T10:25:00Z", "BILLING_COUNTER", dwell_ms=300000, queue_depth=1),
        event("e9", "VIS_STAFF", "ZONE_ENTER", "2026-04-10T10:30:00Z", "BACK_OF_HOUSE", is_staff=True),
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

    metrics = client.get("/stores/ST1008/metrics").json()

    assert metrics["unique_visitors"] == 2
    assert metrics["converted_visitors"] == 1
    assert metrics["conversion_rate"] == 0.5
    assert metrics["abandonment_rate"] == 0.5
    assert metrics["avg_dwell_ms_per_zone"]["SKINCARE"] == 120000


def test_funnel_heatmap_anomalies_and_health() -> None:
    client.post("/events/ingest", json={"events": seed_events()})

    funnel = client.get("/stores/ST1008/funnel").json()
    heatmap = client.get("/stores/ST1008/heatmap").json()
    anomalies = client.get("/stores/ST1008/anomalies").json()
    health = client.get("/health").json()

    assert [stage["count"] for stage in funnel["stages"]] == [2, 2, 2, 1]
    assert heatmap["data_confidence"] == "LOW"
    assert any(zone["zone_id"] == "SKINCARE" for zone in heatmap["zones"])
    assert isinstance(anomalies["anomalies"], list)
    assert health["stores"]["ST1008"]["warning"] == "STALE_FEED"

