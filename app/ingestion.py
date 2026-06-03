from __future__ import annotations

from collections import defaultdict
from threading import Lock

from pydantic import ValidationError

from .errors import StoreUnavailableError
from .models import StoreEvent
from .normalize import normalize_event


class EventStore:
    def __init__(self) -> None:
        self._events_by_id: dict[str, StoreEvent] = {}
        self._events_by_store: dict[str, list[StoreEvent]] = defaultdict(list)
        self._available = True
        self._lock = Lock()

    def ingest(self, raw_events: list[dict]) -> dict:
        accepted = 0
        duplicates = 0
        errors: list[dict] = []

        with self._lock:
            self._ensure_available()
            for index, raw in enumerate(raw_events):
                normalized = normalize_event(raw)
                try:
                    event = StoreEvent.model_validate(normalized)
                except ValidationError as exc:
                    errors.append({"index": index, "event_id": normalized.get("event_id") or raw.get("event_id"), "errors": exc.errors()})
                    continue

                if event.event_id in self._events_by_id:
                    duplicates += 1
                    continue

                self._events_by_id[event.event_id] = event
                self._events_by_store[event.store_id].append(event)
                accepted += 1

        return {"accepted": accepted, "duplicates": duplicates, "rejected": len(errors), "errors": errors}

    def by_store(self, store_id: str) -> list[StoreEvent]:
        self._ensure_available()
        return sorted(self._events_by_store.get(store_id, []), key=lambda event: event.timestamp)

    def all_events(self) -> list[StoreEvent]:
        self._ensure_available()
        events = list(self._events_by_id.values())
        return sorted(events, key=lambda event: event.timestamp)

    def reset(self) -> None:
        with self._lock:
            self._available = True
            self._events_by_id.clear()
            self._events_by_store.clear()

    def set_available(self, available: bool) -> None:
        with self._lock:
            self._available = available

    def _ensure_available(self) -> None:
        if not self._available:
            raise StoreUnavailableError("event store unavailable")


store = EventStore()
