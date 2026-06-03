# PROMPT: Add deterministic unit tests for the non-video parts of the detection pipeline: centroid tracking, polygon zone matching, and JSONL event writing.
# CHANGES MADE: Avoided OpenCV frame processing here because Part A video execution is covered by smoke runs; these tests focus on stable business logic.

import json

from pipeline.emit import JsonlEventWriter, StoreEvent, iso_from_epoch
from pipeline.tracker import CentroidTracker
from pipeline.zones import bbox_anchor, containing_zones, point_in_polygon


def test_centroid_tracker_reuses_track_then_expires_missing_track() -> None:
    tracker = CentroidTracker(max_distance=50, max_misses=1)

    first = tracker.update([((10, 10, 20, 40), 0.8)], frame_ms=0)
    second = tracker.update([((18, 12, 20, 40), 0.7)], frame_ms=100)
    third = tracker.update([], frame_ms=200)
    miss_count = third[0].misses
    fourth = tracker.update([], frame_ms=300)

    assert first[0].visitor_id == second[0].visitor_id
    assert miss_count == 1
    assert fourth == []


def test_zone_helpers_match_bbox_foot_point() -> None:
    zones = [{"zone_id": "PURPLLE_MUM_1076_Z01", "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]]}]

    assert bbox_anchor((10, 10, 20, 40)) == (20.0, 50)
    assert point_in_polygon((20, 50), zones[0]["polygon"])
    assert containing_zones((10, 10, 20, 40), zones) == ["PURPLLE_MUM_1076_Z01"]
    assert containing_zones((110, 110, 20, 40), zones) == []


def test_jsonl_writer_persists_valid_events(tmp_path) -> None:
    out = tmp_path / "events.jsonl"
    event = StoreEvent(
        store_id="ST1076",
        camera_id="CAM_ENTRY_01",
        visitor_id="VIS_1",
        event_type="ENTRY",
        timestamp=iso_from_epoch(1775832000),
        zone_id=None,
        dwell_ms=0,
        is_staff=False,
        confidence=0.9,
        metadata={"queue_depth": None, "sku_zone": None, "session_seq": 1},
    )

    with JsonlEventWriter(out) as writer:
        writer.write(event)

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["event_type"] == "ENTRY"
