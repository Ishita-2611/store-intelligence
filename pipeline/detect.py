from __future__ import annotations

import argparse
import csv
import json
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .emit import JsonlEventWriter, StoreEvent, iso_from_epoch
from .layouts import DEFAULT_LAYOUT, camera_key_for_name, layout_path_for_zip
from .tracker import BBox, CentroidTracker, Track
from .zones import containing_zones

DEFAULT_OUT = Path("outputs/detected_events.jsonl")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Emit Store Intelligence events from CCTV clips.")
    parser.add_argument("--video-zip", required=True, help="ZIP containing CAM *.mp4 files")
    parser.add_argument("--layout", default=str(DEFAULT_LAYOUT), help="Store layout and camera-zone config")
    parser.add_argument("--pos-csv", default=None, help="Optional POS CSV for billing abandonment correlation")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="JSONL destination")
    parser.add_argument("--sample-stride", type=int, default=6, help="Process every Nth frame")
    parser.add_argument("--max-seconds", type=float, default=None, help="Optional cap for quick validation")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    layout_path = layout_path_for_zip(args.video_zip, args.layout) if args.layout == str(DEFAULT_LAYOUT) else Path(args.layout)
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    store_id = layout["store_id"]
    pos_times = load_pos_times(args.pos_csv) if args.pos_csv else {}

    with tempfile.TemporaryDirectory() as tmpdir, JsonlEventWriter(args.out) as writer:
        tmpdir_path = Path(tmpdir)
        with zipfile.ZipFile(args.video_zip) as archive:
            members = [m for m in archive.namelist() if m.lower().endswith(".mp4")]
            for member in sorted(members):
                target = tmpdir_path / Path(member).name
                target.write_bytes(archive.read(member))
                camera_key = camera_key_for_name(target.name)
                camera_cfg = layout["cameras"].get(camera_key, {})
                camera_id = camera_cfg.get("camera_id", camera_key)
                clip_start = _parse_clip_start(camera_cfg.get("clip_start_utc"))
                process_video(
                    target,
                    store_id,
                    camera_id,
                    camera_cfg,
                    clip_start,
                    pos_times.get(store_id, []),
                    writer,
                    args.sample_stride,
                    args.max_seconds,
                )

    print(f"wrote {writer.count} events to {args.out}")


def process_video(
    path: Path,
    store_id: str,
    camera_id: str,
    camera_cfg: dict[str, Any],
    clip_start_epoch: float,
    store_pos_times: list[datetime],
    writer: JsonlEventWriter,
    sample_stride: int,
    max_seconds: float | None,
) -> None:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"could not open {path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    subtractor = cv2.createBackgroundSubtractorMOG2(history=180, varThreshold=32, detectShadows=True)
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    tracker = CentroidTracker(max_distance=float(camera_cfg.get("max_track_distance_px", 140)))

    zones = camera_cfg.get("zones", [])
    frame_no = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_no += 1
        if frame_no % sample_stride != 0:
            continue
        elapsed_s = frame_no / fps
        if max_seconds is not None and elapsed_s > max_seconds:
            break

        frame_ms = int(elapsed_s * 1000)
        detections = detect_people(frame, subtractor, hog, camera_cfg)
        tracks = tracker.update(detections, frame_ms)
        timestamp = iso_from_epoch(clip_start_epoch + elapsed_s)
        for event in events_for_tracks(store_id, camera_id, camera_cfg, zones, tracks, timestamp, frame_ms, store_pos_times):
            writer.write(event)
    cap.release()


def detect_people(
    frame: np.ndarray,
    subtractor: cv2.BackgroundSubtractor,
    hog: cv2.HOGDescriptor,
    camera_cfg: dict[str, Any],
) -> list[tuple[BBox, float]]:
    scale = float(camera_cfg.get("detector_scale", 0.5))
    small = cv2.resize(frame, None, fx=scale, fy=scale)
    rects, weights = _hog_detections(hog, small)
    detections: list[tuple[BBox, float]] = []
    for (x, y, w, h), weight in zip(rects, weights):
        if h < 70 * scale:
            continue
        detections.append((_rescale_bbox((x, y, w, h), 1 / scale), float(min(max(weight, 0.25), 1.0))))

    if len(detections) < int(camera_cfg.get("min_expected_detections", 1)):
        detections.extend(_motion_detections(frame, subtractor, camera_cfg))

    return non_max_suppression(detections, overlap_threshold=0.45)


def events_for_tracks(
    store_id: str,
    camera_id: str,
    camera_cfg: dict[str, Any],
    zones: list[dict],
    tracks: list[Track],
    timestamp: str,
    frame_ms: int,
    store_pos_times: list[datetime],
) -> list[StoreEvent]:
    events: list[StoreEvent] = []
    staff_zones = set(camera_cfg.get("staff_zones", []))
    billing_zones = set(camera_cfg.get("billing_zones", []))
    entry_zone = camera_cfg.get("entry_zone")
    entry_trigger_zones = set(camera_cfg.get("entry_trigger_zones", [entry_zone] if entry_zone else []))
    exit_zone = camera_cfg.get("exit_zone")

    queue_depth = sum(1 for track in tracks if billing_zones & track.zones)

    for track in tracks:
        current_zones = set(containing_zones(track.bbox, zones))
        track.is_staff = track.is_staff or bool(current_zones & staff_zones)
        entered = current_zones - track.zones
        exited = track.zones - current_zones

        if entry_trigger_zones & current_zones and not track.entry_reported and not track.exited:
            track.entry_reported = True
            events.append(_event(store_id, camera_id, track, "ENTRY", timestamp, None, 0, None))
        if exit_zone and exit_zone in current_zones and track.entry_reported and not track.exited:
            track.exited = True
            events.append(_event(store_id, camera_id, track, "EXIT", timestamp, None, 0, None))
        if entry_zone and entry_zone in entered and track.exited:
            events.append(_event(store_id, camera_id, track, "REENTRY", timestamp, None, 0, None))
            track.exited = False

        for zone_id in sorted(entered):
            track.dwell_started_ms[zone_id] = frame_ms
            if zone_id in billing_zones and queue_depth > 0:
                events.append(_event(store_id, camera_id, track, "BILLING_QUEUE_JOIN", timestamp, zone_id, 0, queue_depth))
            else:
                events.append(_event(store_id, camera_id, track, "ZONE_ENTER", timestamp, zone_id, 0, None))

        for zone_id in sorted(exited):
            dwell_ms = max(0, frame_ms - track.dwell_started_ms.get(zone_id, frame_ms))
            events.append(_event(store_id, camera_id, track, "ZONE_EXIT", timestamp, zone_id, dwell_ms, None))
            if zone_id in billing_zones and not has_pos_after(timestamp, store_pos_times):
                events.append(_event(store_id, camera_id, track, "BILLING_QUEUE_ABANDON", timestamp, zone_id, dwell_ms, queue_depth))

        for zone_id in sorted(current_zones):
            dwell_start = track.dwell_started_ms.get(zone_id, frame_ms)
            last_emit = track.dwell_emitted_ms.get(zone_id, dwell_start)
            if frame_ms - dwell_start >= 30_000 and frame_ms - last_emit >= 30_000:
                dwell_ms = frame_ms - dwell_start
                track.dwell_emitted_ms[zone_id] = frame_ms
                events.append(_event(store_id, camera_id, track, "ZONE_DWELL", timestamp, zone_id, dwell_ms, None))

        track.zones = current_zones
    return events


def _event(
    store_id: str,
    camera_id: str,
    track: Track,
    event_type: str,
    timestamp: str,
    zone_id: str | None,
    dwell_ms: int,
    queue_depth: int | None,
) -> StoreEvent:
    track.session_seq += 1
    sku_zone = zone_id
    return StoreEvent(
        store_id=store_id,
        camera_id=camera_id,
        visitor_id=track.visitor_id,
        event_type=event_type,
        timestamp=timestamp,
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=track.is_staff,
        confidence=round(float(track.confidence), 3),
        metadata={"queue_depth": queue_depth, "sku_zone": sku_zone, "session_seq": track.session_seq},
    )


def _hog_detections(hog: cv2.HOGDescriptor, frame: np.ndarray) -> tuple[list[BBox], list[float]]:
    rects, weights = hog.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
    return [tuple(map(int, rect)) for rect in rects], [float(w) for w in weights]


def load_pos_times(path: str) -> dict[str, list[datetime]]:
    by_store: dict[str, list[datetime]] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            store_id = row.get("store_id")
            timestamp = row.get("timestamp")
            if timestamp:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                by_store.setdefault(store_id or "UNKNOWN_STORE", []).append(dt)
                continue

            order_date = row.get("order_date")
            order_time = row.get("order_time")
            if not store_id or not order_date or not order_time:
                continue
            dt = datetime.strptime(f"{order_date} {order_time}", "%d-%m-%Y %H:%M:%S").replace(tzinfo=timezone.utc)
            by_store.setdefault(store_id, []).append(dt)
    for times in by_store.values():
        times.sort()
    return by_store


def has_pos_after(timestamp: str, store_pos_times: list[datetime]) -> bool:
    event_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    window_end = event_time + timedelta(minutes=5)
    return any(event_time <= pos_time <= window_end for pos_time in store_pos_times)


def _motion_detections(frame: np.ndarray, subtractor: cv2.BackgroundSubtractor, camera_cfg: dict[str, Any]) -> list[tuple[BBox, float]]:
    mask = subtractor.apply(frame)
    mask = cv2.medianBlur(mask, 5)
    _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
    kernel = np.ones((7, 7), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_area = int(camera_cfg.get("min_motion_area", 1800))
    max_area = int(camera_cfg.get("max_motion_area", 180000))
    detections: list[tuple[BBox, float]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not min_area <= area <= max_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if h < int(camera_cfg.get("min_person_height_px", 85)):
            continue
        confidence = min(0.85, max(0.25, area / max_area))
        detections.append(((x, y, w, h), confidence))
    return detections


def non_max_suppression(detections: list[tuple[BBox, float]], overlap_threshold: float) -> list[tuple[BBox, float]]:
    if not detections:
        return []
    boxes = np.array([d[0] for d in detections], dtype=float)
    scores = np.array([d[1] for d in detections], dtype=float)
    x1, y1 = boxes[:, 0], boxes[:, 1]
    x2, y2 = boxes[:, 0] + boxes[:, 2], boxes[:, 1] + boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()
    keep: list[int] = []
    while order.size > 0:
        i = order[-1]
        keep.append(int(i))
        order = order[:-1]
        if order.size == 0:
            break
        xx1 = np.maximum(x1[i], x1[order])
        yy1 = np.maximum(y1[i], y1[order])
        xx2 = np.minimum(x2[i], x2[order])
        yy2 = np.minimum(y2[i], y2[order])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        overlap = (w * h) / areas[order]
        order = order[overlap <= overlap_threshold]
    return [detections[i] for i in keep]


def _rescale_bbox(bbox: BBox, factor: float) -> BBox:
    x, y, w, h = bbox
    return int(x * factor), int(y * factor), int(w * factor), int(h * factor)


def _parse_clip_start(value: str | None) -> float:
    if value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    return datetime(2026, 4, 10, 20, 10, tzinfo=timezone.utc).timestamp()


if __name__ == "__main__":
    main()
