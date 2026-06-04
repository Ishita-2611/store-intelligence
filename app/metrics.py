from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .models import StoreEvent


def customer_events(events: list[StoreEvent]) -> list[StoreEvent]:
    return [event for event in events if not event.is_staff]


def session_sets(events: list[StoreEvent]) -> dict[str, set[str]]:
    customers = customer_events(events)
    all_seen = {event.visitor_id for event in customers}
    sets = {
        "entries": set(all_seen),
        "zone_visits": {event.visitor_id for event in customers if event.event_type in {"ZONE_ENTER", "ZONE_DWELL"}},
        "billing": {event.visitor_id for event in customers if event.event_type == "BILLING_QUEUE_JOIN"},
        "abandons": {event.visitor_id for event in customers if event.event_type == "BILLING_QUEUE_ABANDON"},
    }
    sets["purchases"] = sets["billing"] - sets["abandons"]
    return sets


def compute_metrics(events: list[StoreEvent]) -> dict:
    customers = customer_events(events)
    sessions = session_sets(events)
    unique_visitors = len(sessions["entries"])
    converted = len(sessions["purchases"])
    queue_depth = latest_queue_depth(customers)
    abandons = len(sessions["abandons"])

    return {
        "unique_visitors": unique_visitors,
        "conversion_rate": _ratio(converted, unique_visitors),
        "converted_visitors": converted,
        "avg_dwell_ms_per_zone": avg_dwell_by_zone(customers),
        "queue_depth": queue_depth,
        "abandonment_rate": _ratio(abandons, max(len(sessions["billing"]), 1)),
        "event_count": len(customers),
    }


def compute_funnel(events: list[StoreEvent]) -> dict:
    sessions = session_sets(events)
    stages = [
        ("entry", sessions["entries"]),
        ("zone_visit", sessions["zone_visits"]),
        ("billing_queue", sessions["billing"]),
        ("purchase", sessions["purchases"]),
    ]
    previous_count: int | None = None
    response_stages = []
    for name, visitor_ids in stages:
        count = len(visitor_ids)
        dropoff_pct = 0.0 if previous_count in (None, 0) else round((previous_count - count) / previous_count, 4)
        response_stages.append({"stage": name, "count": count, "dropoff_pct": dropoff_pct})
        previous_count = count
    return {"unit": "session", "stages": response_stages}


def compute_heatmap(events: list[StoreEvent]) -> dict:
    customers = customer_events(events)
    visits: dict[str, set[str]] = defaultdict(set)
    dwell_totals: dict[str, int] = defaultdict(int)
    dwell_counts: dict[str, int] = defaultdict(int)

    for event in customers:
        if not event.zone_id:
            continue
        if event.event_type in {"ZONE_ENTER", "ZONE_DWELL", "BILLING_QUEUE_JOIN"}:
            visits[event.zone_id].add(event.visitor_id)
        if event.dwell_ms > 0:
            dwell_totals[event.zone_id] += event.dwell_ms
            dwell_counts[event.zone_id] += 1

    max_visits = max((len(value) for value in visits.values()), default=1)
    max_dwell = max((dwell_totals[zone] / max(dwell_counts[zone], 1) for zone in visits), default=1)
    if max_dwell <= 0:
        max_dwell = 1
    sessions = len(session_sets(events)["entries"])
    zones = []
    for zone_id in sorted(visits):
        visit_count = len(visits[zone_id])
        avg_dwell = dwell_totals[zone_id] / max(dwell_counts[zone_id], 1)
        zones.append(
            {
                "zone_id": zone_id,
                "visit_count": visit_count,
                "avg_dwell_ms": round(avg_dwell, 2),
                "heat_score": round(((visit_count / max_visits) * 70) + ((avg_dwell / max_dwell) * 30), 2),
            }
        )
    return {"data_confidence": "LOW" if sessions < 20 else "NORMAL", "zones": zones}


def compute_anomalies(events: list[StoreEvent]) -> list[dict]:
    customers = customer_events(events)
    if not customers:
        return [{"type": "NO_TRAFFIC", "severity": "INFO", "suggested_action": "No customer events received yet."}]

    anomalies = []
    metrics = compute_metrics(events)
    if metrics["queue_depth"] >= 3:
        severity = "CRITICAL" if metrics["queue_depth"] >= 6 else "WARN"
        anomalies.append(
            {
                "type": "BILLING_QUEUE_SPIKE",
                "severity": severity,
                "suggested_action": "Open an additional billing counter or move staff to checkout.",
            }
        )
    if metrics["unique_visitors"] >= 10 and metrics["conversion_rate"] < 0.05:
        anomalies.append(
            {
                "type": "CONVERSION_DROP",
                "severity": "CRITICAL",
                "suggested_action": "Escalate to store manager; inspect checkout staffing and POS friction immediately.",
            }
        )
    elif metrics["unique_visitors"] >= 20 and metrics["conversion_rate"] < 0.2:
        anomalies.append(
            {
                "type": "CONVERSION_DROP",
                "severity": "WARN",
                "suggested_action": "Check staff availability and billing queue friction.",
            }
        )

    latest = max(event.timestamp for event in customers)
    recent_cutoff = latest - timedelta(minutes=30)
    zones_seen = {event.zone_id for event in customers if event.zone_id}
    recent_zones = {event.zone_id for event in customers if event.zone_id and event.timestamp >= recent_cutoff}
    for zone_id in sorted(zones_seen - recent_zones):
        anomalies.append(
            {
                "type": "DEAD_ZONE",
                "zone_id": zone_id,
                "severity": "INFO",
                "suggested_action": f"Review merchandising or camera coverage for {zone_id}.",
            }
        )
    return anomalies


def health_snapshot(events: list[StoreEvent]) -> dict:
    latest_by_store: dict[str, datetime] = {}
    for event in events:
        latest_by_store[event.store_id] = max(event.timestamp, latest_by_store.get(event.store_id, event.timestamp))

    now = datetime.now(timezone.utc)
    stores = {}
    stale = False
    for store_id, timestamp in latest_by_store.items():
        is_stale = now - timestamp > timedelta(minutes=10)
        stale = stale or is_stale
        stores[store_id] = {"last_event_timestamp": timestamp.isoformat(), "warning": "STALE_FEED" if is_stale else None}
    return {"status": "ok" if not stale else "degraded", "stores": stores}


def latest_queue_depth(events: list[StoreEvent]) -> int:
    queue_events = [event for event in events if event.metadata.queue_depth is not None]
    if not queue_events:
        return 0
    return int(queue_events[-1].metadata.queue_depth or 0)


def avg_dwell_by_zone(events: list[StoreEvent]) -> dict[str, float]:
    totals: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    for event in events:
        if event.zone_id and event.dwell_ms > 0:
            totals[event.zone_id] += event.dwell_ms
            counts[event.zone_id] += 1
    return {zone_id: round(totals[zone_id] / counts[zone_id], 2) for zone_id in sorted(totals)}


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)
