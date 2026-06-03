# PROMPT: Add deterministic tests for pipeline helpers that do not require opening CCTV video files.
# CHANGES MADE: Focused on pure functions and billing event generation so the tests stay fast and stable in CI.

import csv
import zipfile

from pipeline.detect import events_for_tracks, has_pos_after, load_pos_times, non_max_suppression
from pipeline.layouts import camera_key_for_name, layout_path_for_zip
from pipeline.tracker import Track
from pipeline.zones import bbox_anchor, containing_zones


def test_camera_key_and_layout_selection_for_provided_store_zips(tmp_path) -> None:
    store_1_zip = tmp_path / "store-1.zip"
    with zipfile.ZipFile(store_1_zip, "w") as archive:
        archive.writestr("Store 1/CAM 5 - billing.mp4", b"")
    store_2_zip = tmp_path / "store-2.zip"
    with zipfile.ZipFile(store_2_zip, "w") as archive:
        archive.writestr("Store 2/billing_area.mp4", b"")

    assert camera_key_for_name("CAM 5 - billing.mp4") == "CAM_5_BILLING"
    assert camera_key_for_name("billing_area.mp4") == "BILLING_AREA"
    assert layout_path_for_zip(store_1_zip).name == "store_1.json"
    assert layout_path_for_zip(store_2_zip).name == "store_2.json"


def test_zone_helpers_and_non_max_suppression() -> None:
    zones = [{"zone_id": "LEFT", "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]]}]
    detections = [((10, 10, 50, 80), 0.8), ((12, 12, 50, 80), 0.7), ((150, 10, 40, 70), 0.6)]

    assert bbox_anchor((10, 10, 20, 40)) == (20.0, 50)
    assert containing_zones((10, 10, 20, 40), zones) == ["LEFT"]
    assert len(non_max_suppression(detections, overlap_threshold=0.45)) == 2


def test_pos_helpers_support_provided_csv_shape(tmp_path) -> None:
    pos_path = tmp_path / "pos.csv"
    with pos_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["order_id", "order_date", "order_time", "store_id", "product_id", "brand_name", "total_amount"])
        writer.writeheader()
        writer.writerow(
            {
                "order_id": "1",
                "order_date": "10-04-2026",
                "order_time": "12:15:05",
                "store_id": "ST1008",
                "product_id": "399945",
                "brand_name": "Faces Canada",
                "total_amount": "302.33",
            }
        )

    pos_times = load_pos_times(str(pos_path))

    assert "ST1008" in pos_times
    assert has_pos_after("2026-04-10T12:12:00Z", pos_times["ST1008"])
    assert not has_pos_after("2026-04-10T12:20:06Z", pos_times["ST1008"])


def test_first_billing_visitor_gets_queue_join_event() -> None:
    track = Track(
        track_id=1,
        visitor_id="VIS_1",
        bbox=(10, 10, 20, 40),
        confidence=0.9,
        first_seen_ms=0,
        last_seen_ms=0,
    )
    camera_cfg = {"billing_zones": ["BILLING"], "zones": [{"zone_id": "BILLING", "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]]}]}

    events = events_for_tracks("STORE_TEST", "CAM_BILLING", camera_cfg, camera_cfg["zones"], [track], "2026-06-02T10:00:00Z", 1000, [])

    assert [event.event_type for event in events] == ["BILLING_QUEUE_JOIN"]
    assert events[0].metadata["queue_depth"] == 1
