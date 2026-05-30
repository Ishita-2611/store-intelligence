from __future__ import annotations

from typing import Iterable

from .tracker import BBox


Point = tuple[float, float]
Polygon = list[Point]


def bbox_anchor(bbox: BBox) -> Point:
    x, y, w, h = bbox
    return x + w / 2, y + h


def containing_zones(bbox: BBox, zones: Iterable[dict]) -> list[str]:
    point = bbox_anchor(bbox)
    return [zone["zone_id"] for zone in zones if point_in_polygon(point, zone["polygon"])]


def point_in_polygon(point: Point, polygon: Polygon) -> bool:
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        intersects = (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        if intersects:
            inside = not inside
        j = i
    return inside

