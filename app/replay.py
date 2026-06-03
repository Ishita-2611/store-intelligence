from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ingestion import store


DEMO_EVENTS_PATH = Path("data/sample_events.jsonl")


@dataclass
class ReplayState:
    running: bool = False
    total_events: int = 0
    ingested_events: int = 0
    batch_size: int = 25
    interval_ms: int = 700
    started_at: str | None = None
    completed_at: str | None = None
    last_error: str | None = None


class ReplayController:
    def __init__(self) -> None:
        self._state = ReplayState()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._state.__dict__.copy()

    def reset(self) -> dict[str, Any]:
        self.stop()
        with self._lock:
            store.reset()
            self._state = ReplayState()
            return self._state.__dict__.copy()

    def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        with self._lock:
            if self._state.running:
                self._state.running = False
                self._state.completed_at = _now()
            return self._state.__dict__.copy()

    def start(self, batch_size: int = 25, interval_ms: int = 700) -> dict[str, Any]:
        with self._lock:
            if self._state.running:
                return self._state.__dict__.copy()
            events = load_demo_events()
            store.reset()
            self._stop_event.clear()
            self._state = ReplayState(
                running=True,
                total_events=len(events),
                batch_size=max(1, min(batch_size, 100)),
                interval_ms=max(100, min(interval_ms, 5000)),
                started_at=_now(),
            )
            self._thread = threading.Thread(target=self._run, args=(events,), daemon=True)
            self._thread.start()
            return self._state.__dict__.copy()

    def _run(self, events: list[dict[str, Any]]) -> None:
        index = 0
        while index < len(events) and not self._stop_event.is_set():
            with self._lock:
                batch_size = self._state.batch_size
                interval_s = self._state.interval_ms / 1000
            batch = events[index : index + batch_size]
            try:
                result = store.ingest(batch)
                accepted = int(result["accepted"]) + int(result["duplicates"])
            except Exception as exc:  # pragma: no cover - defensive operational path
                with self._lock:
                    self._state.running = False
                    self._state.last_error = str(exc)
                    self._state.completed_at = _now()
                return

            index += batch_size
            with self._lock:
                self._state.ingested_events += accepted
            self._stop_event.wait(interval_s)

        with self._lock:
            self._state.running = False
            self._state.completed_at = _now()


def load_demo_events() -> list[dict[str, Any]]:
    if not DEMO_EVENTS_PATH.exists():
        raise FileNotFoundError(f"demo event stream not found: {DEMO_EVENTS_PATH}")
    return [json.loads(line) for line in DEMO_EVENTS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


replay_controller = ReplayController()
