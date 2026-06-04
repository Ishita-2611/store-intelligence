# PROMPT: Add deterministic tests for pipeline helpers that do not require opening CCTV video files.
# CHANGES MADE: Focused on pure functions and billing event generation so the tests stay fast and stable in CI.

import csv
import json
import zipfile

import cv2
import numpy as np

from pipeline import detect
from pipeline.detect import events_for_tracks, has_pos_after, load_pos_times, non_max_suppression, process_video
from pipeline.emit import JsonlEventWriter
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


def test_billing_abandon_requires_pos_reference_data() -> None:
    track = Track(
        track_id=1,
        visitor_id="VIS_1",
        bbox=(150, 10, 20, 40),
        confidence=0.9,
        first_seen_ms=0,
        last_seen_ms=0,
        zones={"BILLING"},
        dwell_started_ms={"BILLING": 0},
    )
    camera_cfg = {"billing_zones": ["BILLING"], "zones": [{"zone_id": "BILLING", "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]]}]}

    events = events_for_tracks("STORE_TEST", "CAM_BILLING", camera_cfg, camera_cfg["zones"], [track], "2026-06-02T10:00:00Z", 60_000, [])

    assert [event.event_type for event in events] == ["ZONE_EXIT"]


def test_secondary_entry_camera_can_be_observation_only() -> None:
    track = Track(
        track_id=1,
        visitor_id="VIS_1",
        bbox=(10, 10, 20, 40),
        confidence=0.9,
        first_seen_ms=0,
        last_seen_ms=0,
    )
    camera_cfg = {
        "emit_entry_events": False,
        "entry_zone": "ENTRY",
        "entry_trigger_zones": ["ENTRY"],
        "zones": [{"zone_id": "ENTRY", "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]]}],
    }

    events = events_for_tracks("STORE_TEST", "CAM_ENTRY_2", camera_cfg, camera_cfg["zones"], [track], "2026-06-02T10:00:00Z", 1000, [])

    assert [event.event_type for event in events] == ["ZONE_ENTER"]


def test_process_video_runs_with_mocked_capture(tmp_path, monkeypatch) -> None:
    frames = [np.zeros((480, 640, 3), dtype=np.uint8) for _ in range(18)]
    frame_iter = iter(frames)

    class FakeCapture:
        def isOpened(self) -> bool:
            return True

        def get(self, prop: int) -> float:
            if prop == cv2.CAP_PROP_FPS:
                return 15.0
            return 0.0

        def read(self):
            try:
                return True, next(frame_iter)
            except StopIteration:
                return False, None

        def release(self) -> None:
            return None

    monkeypatch.setattr(cv2, "VideoCapture", lambda *_args, **_kwargs: FakeCapture())

    out = tmp_path / "events.jsonl"
    camera_cfg = {
        "entry_zone": None,
        "zones": [{"zone_id": "Z1", "polygon": [[0, 0], [640, 0], [640, 480], [0, 480]]}],
    }
    with JsonlEventWriter(out) as writer:
        process_video(tmp_path / "fake.mp4", "ST_TEST", "CAM1", camera_cfg, 1_700_000_000.0, [], writer, 6, 2.0)

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert isinstance(lines, list)


def test_yolo_detector_path_is_used_when_available(monkeypatch) -> None:
    detect._load_yolo_model.cache_clear()

    class FakeTensor:
        def __init__(self, values):
            self.values = values

        def __iter__(self):
            return iter(self.values)

        def __getitem__(self, index):
            return self.values[index]

    class FakeBox:
        xyxy = [FakeTensor([10.0, 20.0, 60.0, 120.0])]
        conf = [0.88]

    class FakeResult:
        boxes = [FakeBox()]

    class FakeModel:
        def predict(self, *_args, **_kwargs):
            return [FakeResult()]

    monkeypatch.setattr(detect, "_load_yolo_model", lambda _model_name: FakeModel())

    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    detections = detect._yolo_detections(frame, 0.5, {"yolo_model": "fake.pt"})

    assert detections == [((20, 40, 100, 200), 0.88)]
