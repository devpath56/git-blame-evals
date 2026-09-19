"""Ownership registry: which run does an event actually belong to?

The registry is a dict keyed by (session_id, turn_id, event_id).
The audit is a membership test against that dict. Nothing more.

Grain note: `event_id` is read off the event body (`event.id`). `session_id`
and `turn_id` are ingest-context -- they are the stream this event arrived
on, not fields inside the payload. Ownership is established by provenance,
which is exactly why a label-keyed eval can lose it.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class OwnershipKey:
    session_id: str
    turn_id: str
    event_id: str


@dataclass(frozen=True)
class OwnershipRecord:
    key: OwnershipKey
    event_type: str
    thread_id: str | None
    created_at: str | None
    sequence: int


class OwnershipRegistry:
    """Live ownership of every event this auditor has seen arrive."""

    def __init__(self) -> None:
        self._by_key: dict[OwnershipKey, OwnershipRecord] = {}
        self._by_event: dict[str, list[OwnershipKey]] = {}
        self._sequence = 0

    def record(self, session_id: str, turn_id: str, event: dict) -> OwnershipRecord | None:
        """Register one live event. Frames with no id are not ownable."""
        event_id = event.get("id")
        if not event_id:
            return None
        key = OwnershipKey(session_id, turn_id, event_id)
        if key in self._by_key:
            return self._by_key[key]
        self._sequence += 1
        record = OwnershipRecord(
            key=key,
            event_type=event.get("type", ""),
            thread_id=event.get("thread_id"),
            created_at=event.get("created_at"),
            sequence=self._sequence,
        )
        self._by_key[key] = record
        self._by_event.setdefault(event_id, []).append(key)
        return record

    def owns(self, session_id: str, turn_id: str, event_id: str) -> bool:
        """The whole audit, in one line."""
        return OwnershipKey(session_id, turn_id, event_id) in self._by_key

    def resolve(self, event_id: str) -> list[OwnershipKey]:
        """True owners of an event id, across every session ingested."""
        return list(self._by_event.get(event_id, []))

    def sessions(self) -> set[str]:
        return {key.session_id for key in self._by_key}

    def __len__(self) -> int:
        return len(self._by_key)
