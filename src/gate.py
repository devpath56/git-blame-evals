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
ANCHOR = (
    "TrueForge replay of the target session (independent of this receipt). "
    "Proves every scored id is an event TrueForge attributes to the target; "
    "it does not prove the tool scored those and only those -- that remains "
    "the tool's own report."
)


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


def decide(reason: str) -> tuple[dict, str]:
    """The HUMAN decides. This script never decides on its own.

    Interactive prompt is the real gate. AUDITOR_AUTO_DECISION is a test
    affordance for timed and unattended runs -- it is recorded as such.
    """
    auto = os.environ.get("AUDITOR_AUTO_DECISION", "").strip().lower()
    if auto in ("allow", "deny"):
        print(f"  [AUDITOR_AUTO_DECISION={auto}] test affordance -- not a human decision")
        return _choice(auto, reason), "auto"
    if not sys.stdin.isatty():
        raise SystemExit(
            "no TTY and AUDITOR_AUTO_DECISION is unset -- refusing to decide for you.\n"
            "Run interactively, or set AUDITOR_AUTO_DECISION=allow|deny for unattended runs."
        )
    while True:
        answer = input("  approve / reject > ").strip().lower()
        if answer in ("approve", "allow", "a"):
            return _choice("allow", reason), "human"
        if answer in ("reject", "deny", "r"):
            return _choice("deny", reason), "human"
        print("  type 'approve' or 'reject'")


def _choice(status: str, reason: str) -> dict:
    return {"status": "allow"} if status == "allow" else {"status": "deny", "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser(description="Approval gate for an UNEVALUABLE verdict")
    parser.add_argument("--target", required=True, help="Session id with an UNEVALUABLE receipt")
    parser.add_argument("--reason", default="foreign spans present; scoring refused")
    args = parser.parse_args()

    receipt = json.loads(verdict.receipt_path(args.target, verdict.UNEVALUABLE).read_text())
    before = verdict.verdict_region(receipt)
    prior = receipt.get("approval") or {}
    if prior.get("status") in (verdict.APPROVED, verdict.DENIED):
        raise SystemExit(
            f"already decided: {prior['decision']} at {prior.get('decided_at')} -- refusing to "
            "decide twice on one receipt. Re-run the audit to gate a fresh verdict."
        )
    ensure_provider()
    session_id = open_gate_session()
    turn_id, pauses, _ = drive(session_id, [{"type": "user.message", "content": f"Score the evaluation for session={args.target}"}])
    if not pauses:
        raise SystemExit("gate did not pause -- refusing to score unapproved (see README)")

    thread_id, tool_call = pauses[0]
    verdict.persist(verdict.open_approval(receipt))
    present(receipt, tool_call)
    choice, source = decide(args.reason)
    print(f"\n>>> decision: {choice['status'].upper()}  (source: {source})\n")

    approval = [{"type": "user.tool_approval", "thread_id": thread_id, "tool_call_id": tool_call["id"], "approval": choice}]
    _, _, results = drive(session_id, approval, previous_turn_id=turn_id)

    # ANCHOR: ownership re-read from TrueForge, not from the receipt being checked.
    witnessed = {
        (row.get("event") or {}).get("id")
        for row in stream.replay_session_events(args.target)
    } - {None}
    verdict.record_approval(receipt, choice, results, witnessed, ANCHOR)
    receipt["approval"]["decision_source"] = source

    if verdict.verdict_region(receipt) != before:
        raise SystemExit("gate mutated the verdict region -- refusing to write")

    print(json.dumps({"approval": receipt["approval"], "outcome": receipt["outcome"]}, indent=2))
    print(f"\nreceipt: {verdict.persist(receipt)}")
    print(f"ledger : {verdict.log_event(receipt)}")
    return 0 if choice["status"] == "allow" else 3


if __name__ == "__main__":
    raise SystemExit(main())
