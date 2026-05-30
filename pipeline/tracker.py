from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot


BBox = tuple[int, int, int, int]


@dataclass
class Track:
    track_id: int
    visitor_id: str
    bbox: BBox
    confidence: float
    first_seen_ms: int
    last_seen_ms: int
    misses: int = 0
    zones: set[str] = field(default_factory=set)
    dwell_started_ms: dict[str, int] = field(default_factory=dict)
    dwell_emitted_ms: dict[str, int] = field(default_factory=dict)
    session_seq: int = 0
    entry_state: str | None = None
    entry_reported: bool = False
    exited: bool = False
    is_staff: bool = False

    @property
    def centroid(self) -> tuple[float, float]:
        x, y, w, h = self.bbox
        return x + w / 2, y + h / 2


class CentroidTracker:
    def __init__(self, max_distance: float = 120.0, max_misses: int = 12) -> None:
        self.max_distance = max_distance
        self.max_misses = max_misses
        self._next_id = 1
        self.tracks: dict[int, Track] = {}

    def update(self, detections: list[tuple[BBox, float]], frame_ms: int) -> list[Track]:
        unmatched_track_ids = set(self.tracks)
        assignments: list[tuple[int, BBox, float]] = []

        for bbox, confidence in detections:
            cx, cy = _centroid(bbox)
            best_track_id: int | None = None
            best_dist = self.max_distance
            for track_id in list(unmatched_track_ids):
                tx, ty = self.tracks[track_id].centroid
                dist = hypot(cx - tx, cy - ty)
                if dist < best_dist:
                    best_track_id = track_id
                    best_dist = dist
            if best_track_id is None:
                track_id = self._next_id
                self._next_id += 1
                visitor_id = f"VIS_{track_id:06x}"
                self.tracks[track_id] = Track(track_id, visitor_id, bbox, confidence, frame_ms, frame_ms)
            else:
                unmatched_track_ids.remove(best_track_id)
                assignments.append((best_track_id, bbox, confidence))

        for track_id, bbox, confidence in assignments:
            track = self.tracks[track_id]
            track.bbox = bbox
            track.confidence = confidence
            track.last_seen_ms = frame_ms
            track.misses = 0

        for track_id in list(unmatched_track_ids):
            track = self.tracks[track_id]
            track.misses += 1
            if track.misses > self.max_misses:
                del self.tracks[track_id]

        return list(self.tracks.values())


def _centroid(bbox: BBox) -> tuple[float, float]:
    x, y, w, h = bbox
    return x + w / 2, y + h / 2
