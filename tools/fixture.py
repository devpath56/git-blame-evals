"""Deterministic contamination fixture: sessions whose span labels collide.

Every session runs the same number of model calls, so "LLM call 9" exists in
all of them and points at a different real event in each. That is CF-262.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import harness, stream  # noqa: E402
from src.registry import OwnershipRegistry  # noqa: E402
from tools.provision import ensure_provider, ensure_session  # noqa: E402

FIXTURE_SESSIONS = ("target-run", "neighbour-one", "neighbour-two")


def build(turns: int):
    """Run every fixture session live, ingesting all of them into one registry.

    The auditor witnesses all three runs, which is what lets it name the TRUE
    owner of a foreign span instead of only rejecting it.
    """
    ensure_provider()
    registry = OwnershipRegistry()
    spans_by_session, session_ids = {}, []
    for label in FIXTURE_SESSIONS:
        session_id = ensure_session(label)
        session_ids.append(session_id)
        for index in range(turns):
            stream.ingest_live_turn(registry, session_id, f"Probe {index + 1}: reply briefly.")
        spans_by_session[session_id] = harness.label_spans(
            session_id, stream.replay_session_events(session_id)
        )
    return session_ids[0], session_ids, registry, spans_by_session
