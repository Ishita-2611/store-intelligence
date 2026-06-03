from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any


EVENT_TYPE_MAP = {
    "entry": "ENTRY",
    "exit": "EXIT",
    "zone_entered": "ZONE_ENTER",
    "zone_exited": "ZONE_EXIT",
    "queue_completed": "BILLING_QUEUE_JOIN",
    "queue_abandoned": "BILLING_QUEUE_ABANDON",
}


def normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    if "event_id" in raw and "visitor_id" in raw and "timestamp" in raw:
        return raw

    event_type = EVENT_TYPE_MAP.get(str(raw.get("event_type", "")).lower())
    if not event_type:
        return raw

    timestamp = first_present(raw, "timestamp", "event_timestamp", "event_time", "queue_join_ts", "queue_exit_ts")
    store_id = normalize_store_id(first_present(raw, "store_id", "store_code") or "UNKNOWN_STORE")
    visitor_id = normalize_visitor_id(first_present(raw, "visitor_id", "id_token", "track_id") or stable_id(raw))
    zone_id = raw.get("zone_id")
    queue_depth = raw.get("queue_position_at_join")
    dwell_ms = int(float(raw.get("wait_seconds") or 0) * 1000)

    return {
        "event_id": first_present(raw, "event_id", "queue_event_id") or stable_id(raw),
        "store_id": store_id,
        "camera_id": str(raw.get("camera_id") or "UNKNOWN_CAMERA").upper(),
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": bool(raw.get("is_staff", False)),
        "confidence": float(raw.get("confidence", 0.7)),
        "metadata": {
            "queue_depth": int(queue_depth) if queue_depth is not None else None,
            "sku_zone": raw.get("zone_name") or zone_id,
            "session_seq": int(raw.get("session_seq") or 1),
        },
    }


def first_present(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_store_id(value: Any) -> str:
    text = str(value)
    if text.lower().startswith("store_"):
        return f"ST{text.split('_', 1)[1]}"
    return text


def normalize_visitor_id(value: Any) -> str:
    text = str(value)
    if text.startswith("VIS_") or text.startswith("ID_"):
        return text
    return f"VIS_{text}"


def stable_id(raw: dict[str, Any]) -> str:
    parts = [
        str(raw.get("event_type", "")),
        str(first_present(raw, "store_id", "store_code") or ""),
        str(first_present(raw, "id_token", "track_id") or ""),
        str(first_present(raw, "event_timestamp", "event_time", "queue_join_ts", "queue_exit_ts") or datetime.utcnow().isoformat()),
        str(raw.get("zone_id", "")),
    ]
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"evt_{digest}"
