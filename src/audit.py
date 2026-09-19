"""Audit a TrueForge run: live ownership in, exactly one verdict out.

Two independent TrueForge views are compared, which is what keeps the audit
from being circular:
  ownership  <- the LIVE SSE stream (what arrived, and on whose stream)
  the claim  <- REPLAY (what the eval says it scored)

--clean         the eval groups spans by event id        -> SCORED
--contaminated  the eval groups spans by label (CF-262)  -> UNEVALUABLE
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import harness, stream, verdict  # noqa: E402
from src.registry import OwnershipRegistry  # noqa: E402
from tools import fixture  # noqa: E402


def run_clean(session_id: str, prompt: str):
    registry = OwnershipRegistry()
    turn_id, frames = stream.ingest_live_turn(registry, session_id, prompt)
    if not turn_id:
        raise SystemExit("no turn.created on the live stream -- nothing to own")
    spans = harness.label_spans(session_id, stream.replay_session_events(session_id))
    claimed = harness.claim_by_event_id(spans)
    print(f"live SSE  : {frames} frames -> {len(registry)} owned events (turn {turn_id})")
    print(f"eval claim: {len(claimed)} spans grouped by EVENT ID\n")
    return registry, session_id, claimed, 0


def run_contaminated(turns: int):
    target, session_ids, registry, spans_by_session = fixture.build(turns)
    claimed = harness.claim_by_label(target, spans_by_session)
    planted = sum(1 for span in claimed if span["_fixture_owner"] != target)
    print(f"fixture   : {len(session_ids)} sessions, {turns} model calls each")
    print(f"live SSE  : {len(registry)} owned events across {len(registry.sessions())} sessions")
    print(f"eval claim: {len(claimed)} spans grouped by LABEL -- {planted} planted foreign\n")
    return registry, target, claimed, planted


def main() -> int:
    parser = argparse.ArgumentParser(description="TrueForge eval-grain auditor")
    parser.add_argument("--session", help="Target session id (clean mode)")
    parser.add_argument("--contaminated", action="store_true", help="Build the CF-262 fixture")
    parser.add_argument("--turns", type=int, default=int(os.environ.get("FIXTURE_TURNS", "9")))
    parser.add_argument("--prompt", default="Say hello in one sentence.")
    args = parser.parse_args()

    if args.contaminated:
        registry, target, claimed, planted = run_contaminated(args.turns)
    elif args.session:
        registry, target, claimed, planted = run_clean(args.session, args.prompt)
    else:
        parser.error("pass --session <id> or --contaminated")

    result = verdict.audit(registry, target, claimed)
    result["planted_foreign_events"] = planted
    result["foreign_recall"] = 1.0 if planted == result["foreign_events"] else result["foreign_events"] / max(planted, 1)

    print(verdict.render(result))
    print(f"\nreceipt: {verdict.persist(result)}")
    print(f"ledger : {verdict.log_event(result)}")
    if planted:
        print(f"recall : {result['foreign_events']}/{planted} planted foreign spans listed")
    return 0 if result["verdict"] == verdict.SCORED else 2


if __name__ == "__main__":
    raise SystemExit(main())
