"""Route UNEVALUABLE to TrueForge's NATIVE approval gate.

Scoring is an MCP tool so TrueForge itself can pause it. The pause, the human
decision and the resume are TrueForge's own tool-approval flow -- not a prompt
we invented. Enforcement lives in the tool (owned spans only), so an approval
can widen nothing: it permits scoring the owned subset, never the foreign one.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import stream, verdict  # noqa: E402
from tools.provision import MODEL, PROVIDER, ensure_provider  # noqa: E402

MCP_SERVER = "eval-scorer"


def open_gate_session() -> str:
    spec = {
        "model": {"name": f"{PROVIDER}/{MODEL}"},
        "instructions": "Call score_eval for the session named in the request.",
        "mcp_servers": [
            {
                "name": MCP_SERVER,
                "enable_tools": ["score_eval"],
                "preload_tools": ["score_eval"],
                "require_approval_for_tools": ["score_eval"],
            }
        ],
    }
    session = stream.api("POST", "/api/v1/sessions", {"agent": {"spec": spec}, "metadata": {"name": "score-gate"}})
    return (session.get("data") or session)["id"]


def drive(session_id: str, turn_input: list[dict], previous_turn_id: str | None = None):
    """Run a turn to completion, returning its turn id, pauses and tool results."""
    turn_id, pauses, results = None, [], []
    with stream.stream_turn(session_id, turn_input, previous_turn_id) as response:
        for event in stream.iter_sse(response):
            kind = event.get("type")
            if kind == "turn.created":
                turn_id = event.get("turn_id")
            elif kind == "tool.approval_required":
                pauses.extend((event["thread_id"], call) for call in event["tool_calls"])
            elif kind == "tool.response":
                results.append(event)
    return turn_id, pauses, results


def present(receipt: dict, tool_call: dict, shown: int = 3) -> None:
    """The approval UX: the finding, not a yes/no with no evidence."""
    foreign = receipt["foreign"]
    print("=" * 72)
    print("TrueForge paused a tool call -- APPROVAL REQUIRED")
    print("=" * 72)
    print(f"  tool           : score_eval  (call {tool_call['id']})")
    print(f"  target session : {receipt['target']}")
    print(f"  claimed spans  : {receipt['claimed_events']}")
    print(f"  owned spans    : {receipt['owned_events']}   <- scorable")
    print(f"  FOREIGN spans  : {receipt['foreign_events']}   <- never scored\n")
    print(f"  {receipt['foreign_events']} of {receipt['claimed_events']} claimed spans belong to other sessions:")
    for span in foreign[:shown]:
        print(f"    {span['label']:<12} {span['event_id']}  ->  session {span['true_session_id']}")
    if len(foreign) > shown:
        print(f"    ... and {len(foreign) - shown} more, all listed in the receipt")
    print("\n  Approve scoring anyway?")
    print("  APPROVE scores the owned spans ONLY; foreign spans stay excluded.")
    print("  REJECT scores nothing.")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description="Approval gate for an UNEVALUABLE verdict")
    parser.add_argument("--target", required=True, help="Session id with an UNEVALUABLE receipt")
    decision = parser.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", action="store_true")
    parser.add_argument("--reason", default="foreign spans present; scoring refused")
    args = parser.parse_args()

    receipt = json.loads(verdict.receipt_path(args.target, verdict.UNEVALUABLE).read_text())
    ensure_provider()
    session_id = open_gate_session()
    turn_id, pauses, _ = drive(session_id, [{"type": "user.message", "content": f"Score the evaluation for session={args.target}"}])
    if not pauses:
        raise SystemExit("gate did not pause -- refusing to score unapproved (see README)")

    thread_id, tool_call = pauses[0]
    present(receipt, tool_call)
    choice = {"status": "allow"} if args.approve else {"status": "deny", "reason": args.reason}
    print(f"\n>>> human decision: {choice['status'].upper()}\n")

    approval = [{"type": "user.tool_approval", "thread_id": thread_id, "tool_call_id": tool_call["id"], "approval": choice}]
    _, _, results = drive(session_id, approval, previous_turn_id=turn_id)
    outcome = verdict.record_approval(receipt, choice, results)
    print(json.dumps(outcome["approval"], indent=2))
    print(f"\nreceipt: {verdict.persist(receipt)}")
    print(f"ledger : {verdict.log_event(receipt)}")
    return 0 if args.approve else 3


if __name__ == "__main__":
    raise SystemExit(main())
