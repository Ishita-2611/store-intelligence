# PROMPT: Create tests that validate the Store Intelligence Part A event schema and catch invalid event types, timestamps, confidence values, and JSON serialization.
# CHANGES MADE: Kept the test focused on schema behavior only so it remains stable while the computer-vision detector is iterated.

import json

import pytest

from pipeline.emit import StoreEvent


def test_store_event_serializes_required_schema() -> None:
    event = StoreEvent(
        store_id="ST1076",
        camera_id="CAM_ENTRY_01",
        visitor_id="VIS_000001",
        event_type="ENTRY",
        timestamp="2026-04-10T20:10:00Z",
        zone_id=None,
        dwell_ms=0,
        is_staff=False,
        confidence=0.81,
        metadata={"queue_depth": None, "sku_zone": None, "session_seq": 1},
    )

    payload = json.loads(event.to_json())

    assert payload["event_id"]
    assert payload["store_id"] == "ST1076"
    assert payload["event_type"] == "ENTRY"
    assert payload["metadata"]["session_seq"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_type", "BAD_EVENT"),
        ("timestamp", "not-a-date"),
        ("confidence", 1.5),
        ("dwell_ms", -1),
    ],
)
def test_store_event_rejects_invalid_values(field: str, value: object) -> None:
    kwargs = dict(
        store_id="ST1076",
        camera_id="CAM_ENTRY_01",
        visitor_id="VIS_000001",
        event_type="ENTRY",
        timestamp="2026-04-10T20:10:00Z",
        zone_id=None,
        dwell_ms=0,
        is_staff=False,
        confidence=0.81,
        metadata={"queue_depth": None, "sku_zone": None, "session_seq": 1},
    )
    kwargs[field] = value

    with pytest.raises(ValueError):
        StoreEvent(**kwargs).to_json()
