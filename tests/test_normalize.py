# PROMPT: Add tests for compatibility normalization from the provided sample_events JSONL shapes into the canonical API schema.
# CHANGES MADE: Covered entry, zone, queue, canonical pass-through, and unknown-event fallback cases without depending on the full sample file.

from app.normalize import normalize_event


def test_normalizes_entry_event_from_store_code_shape() -> None:
    normalized = normalize_event(
        {
            "event_type": "entry",
            "id_token": "ID_60001",
            "store_code": "store_1076",
            "camera_id": "cam1",
            "event_timestamp": "2026-03-08T18:10:05.120000",
            "is_staff": False,
        }
    )

    assert normalized["store_id"] == "ST1076"
    assert normalized["camera_id"] == "CAM1"
    assert normalized["visitor_id"] == "ID_60001"
    assert normalized["event_type"] == "ENTRY"
    assert normalized["metadata"]["session_seq"] == 1


def test_normalizes_zone_and_queue_events() -> None:
    zone = normalize_event(
        {
            "event_type": "zone_entered",
            "track_id": 101,
            "store_id": "ST1076",
            "camera_id": "CAM2",
            "zone_id": "PURPLLE_MUM_1076_Z01",
            "zone_name": "Left Shelf",
            "event_time": "2026-03-08T18:10:45.280000",
        }
    )
    queue = normalize_event(
        {
            "queue_event_id": "queue-1",
            "event_type": "queue_completed",
            "track_id": 102,
            "store_id": "ST1076",
            "camera_id": "PURPLLE_MUM_1076_CAM6",
            "zone_id": "PURPLLE_MUM_1076_Z_BILLING_01",
            "zone_name": "Billing Counter Queue",
            "queue_join_ts": "2026-03-08T18:13:05.080000",
            "wait_seconds": 8,
            "queue_position_at_join": 2,
        }
    )

    assert zone["event_type"] == "ZONE_ENTER"
    assert zone["visitor_id"] == "VIS_101"
    assert zone["metadata"]["sku_zone"] == "Left Shelf"
    assert queue["event_type"] == "BILLING_QUEUE_JOIN"
    assert queue["dwell_ms"] == 8000
    assert queue["metadata"]["queue_depth"] == 2


def test_canonical_events_pass_through_and_unknown_events_are_unchanged() -> None:
    canonical = {"event_id": "evt-1", "visitor_id": "VIS_1", "timestamp": "2026-06-02T10:00:00Z"}
    unknown = {"event_type": "not_in_catalogue", "store_code": "store_1076"}

    assert normalize_event(canonical) is canonical
    assert normalize_event(unknown) == unknown
