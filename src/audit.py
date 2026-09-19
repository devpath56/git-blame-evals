"""Audit one TrueForge session: live ownership in, exactly one verdict out.

Two independent TrueForge views are compared, which is what keeps the audit
from being circular:
  ownership  <- the LIVE SSE stream (what arrived, and on whose stream)
  the claim  <- REPLAY (what the eval says it scored), grouped by event id
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import stream, verdict  # noqa: E402
from src.registry import OwnershipRegistry  # noqa: E402


def ingest_live_turn(registry: OwnershipRegistry, session_id: str, prompt: str):
    """Consume the live SSE stream of a new turn straight into the registry."""
    turn_id, frames = None, 0
    with stream.stream_turn(session_id, [{"type": "user.message", "content": prompt}]) as response:
        for event in stream.iter_sse(response):
            frames += 1
            if event.get("type") == "turn.created":
                turn_id = event.get("turn_id")
            if turn_id:
                registry.record(session_id, turn_id, event)
    return turn_id, frames


def claim_from_replay(session_id: str) -> list[dict]:
    """The clean judge: group the spans it scored by EVENT ID -- the real grain."""
    groups: dict[str, dict] = {}
    for row in stream.replay_session_events(session_id):
        event_id = (row.get("event") or {}).get("id")
        if not event_id:
            continue
        group = groups.setdefault(event_id, {"event_id": event_id, "turn_id": row["turn_id"], "spans": 0})
        group["spans"] += 1
    return list(groups.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="TrueForge eval-grain auditor (clean path)")
    parser.add_argument("--session", required=True, help="Target session id to audit")
    parser.add_argument("--prompt", default="Say hello in one sentence.")
    args = parser.parse_args()

    registry = OwnershipRegistry()
    turn_id, frames = ingest_live_turn(registry, args.session, args.prompt)
    if not turn_id:
        raise SystemExit("no turn.created on the live stream -- nothing to own")

    claimed = claim_from_replay(args.session)
    result = verdict.audit(registry, args.session, claimed)
    result["turn_id"] = turn_id
    result["live_sse_frames"] = frames

    print(f"live SSE  : {frames} frames -> {len(registry)} owned events (turn {turn_id})")
    print(f"eval claim: {len(claimed)} spans grouped by event id (replay)\n")
    print(verdict.render(result))
    print(f"\nreceipt: {verdict.persist(result)}")
    return 0 if result["verdict"] == verdict.SCORED else 2


if __name__ == "__main__":
    raise SystemExit(main())
