from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


EventType = Literal[
    "ENTRY",
    "EXIT",
    "ZONE_ENTER",
    "ZONE_EXIT",
    "ZONE_DWELL",
    "BILLING_QUEUE_JOIN",
    "BILLING_QUEUE_ABANDON",
    "REENTRY",
]


class EventMetadata(BaseModel):
    queue_depth: int | None = None
    sku_zone: str | None = None
    session_seq: int = Field(ge=1)


class StoreEvent(BaseModel):
    event_id: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    visitor_id: str = Field(min_length=1)
    event_type: EventType
    timestamp: datetime
    zone_id: str | None = None
    dwell_ms: int = Field(ge=0)
    is_staff: bool
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_zulu_timestamp(cls, value: Any) -> Any:
        if isinstance(value, str) and value.endswith("Z"):
            return value.replace("Z", "+00:00")
        return value

    @field_validator("timestamp")
    @classmethod
    def default_naive_timestamp_to_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class IngestRequest(BaseModel):
    events: list[dict[str, Any]] = Field(max_length=500)


class IngestResponse(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    errors: list[dict[str, Any]]
