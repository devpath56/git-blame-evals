"""The verdict contract. Exactly one terminal verdict per audit."""
import json
import os
import pathlib
import time
import uuid

SCORED = "SCORED"
UNEVALUABLE = "UNEVALUABLE"
UNWITNESSED = "UNWITNESSED"


def audit(registry, target_session: str, claimed: list[dict]) -> dict:
    """Membership-test every claimed span against the live ownership registry.

    `claimed` carries what the eval says it scored: {turn_id, event_id}.

    A non-owned span splits two ways, because they indict different parties:
      FOREIGN      the registry holds it under another session -- the EVAL is wrong
      UNWITNESSED  the registry never saw it at all -- the AUDITOR cannot tell
    Folding the second into the first would let a dropped stream read as
    contamination, which is a different and defamatory answer.
    """
    owned, foreign, unwitnessed = [], [], []
    for span in claimed:
        turn_id, event_id = span.get("turn_id"), span.get("event_id")
        if registry.owns(target_session, turn_id, event_id):
            owned.append(span)
            continue
        true_owners = registry.resolve(event_id)
        record = {
            "label": span.get("label"),
            "event_id": event_id,
            "claimed_turn_id": turn_id,
            "true_session_id": true_owners[0].session_id if true_owners else None,
            "true_turn_id": true_owners[0].turn_id if true_owners else None,
        }
        (foreign if true_owners else unwitnessed).append(record)

    if not claimed:
        outcome, reason = UNEVALUABLE, "empty claim: nothing to verify"
    elif foreign:
        outcome, reason = UNEVALUABLE, None
    elif unwitnessed:
        outcome, reason = UNWITNESSED, "claimed spans absent from the ownership registry"
    else:
        outcome, reason = SCORED, None

    return {
        "verdict": outcome,
        "reason": reason,
        "target": target_session,
        "claimed_events": len(claimed),
        "owned_events": len(owned),
        "owned_event_ids": [span["event_id"] for span in owned],
        "foreign_events": len(foreign),
        "foreign": foreign,
        "unwitnessed_events": len(unwitnessed),
        "unwitnessed": unwitnessed,
        "unwitnessed_event_ids": [span["event_id"] for span in unwitnessed],
        "evidence_source": "TrueForge replay",
        "registry_size": len(registry),
        "registry_sessions": sorted(registry.sessions()),
        "emitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def render(result: dict) -> str:
    """The verdict contract, exactly as the spec fixes it."""
    lines = [
        result["verdict"],
        f"  target: {result['target']}",
        f"  claimed_events: {result['claimed_events']}",
        f"  owned_events: {result['owned_events']}",
        f"  foreign_events: {result['foreign_events']}",
        f"  unwitnessed_events: {result['unwitnessed_events']}",
    ]
    if result.get("reason"):
        lines.append(f"  reason: {result['reason']}")
    if result["verdict"] == UNEVALUABLE:
        lines.append("  foreign:")
        lines.extend(f"    {json.dumps(span)}" for span in result["foreign"])
        if result["unwitnessed_event_ids"]:
            lines.append(f"  unwitnessed_event_ids: {json.dumps(result['unwitnessed_event_ids'])}")
        lines.append("  next_state: TrueForge approval required")
    elif result["verdict"] == UNWITNESSED:
        lines.append("  unwitnessed:")
        lines.extend(f"    {json.dumps(span)}" for span in result["unwitnessed"])
    else:
        lines.append(f"  evidence_source: {result['evidence_source']}")
    return "\n".join(lines)


def _receipt_dir() -> pathlib.Path:
    directory = os.environ.get("AUDITOR_RECEIPT_DIR", "").strip()
    if not directory:
        raise SystemExit("AUDITOR_RECEIPT_DIR is not set -- see README.md")
    path = pathlib.Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    return path


def receipt_path(target: str, which: str) -> pathlib.Path:
    return _receipt_dir() / f"{target}-{which.lower()}.json"


def record_approval(receipt: dict, decision: dict, tool_results: list[dict]) -> dict:
    """Record the human decision AND prove the excluded set stayed excluded."""
    scored: dict = {}
    for event in tool_results:
        try:
            scored = json.loads(event.get("content") or "{}")
        except json.JSONDecodeError:
            scored = {"raw": event.get("content")}
    excluded = [span["event_id"] for span in receipt["foreign"]]
    scored_ids = set(scored.get("scored_event_ids", []))
    receipt["approval"] = {
        "gate": "TrueForge tool.approval_required",
        "decision": decision["status"],
        "reason": decision.get("reason"),
        "excluded_count": len(excluded),
        "excluded_event_ids": excluded,
        "scored_event_ids": sorted(scored_ids),
        "foreign_spans_scored": sorted(scored_ids & set(excluded)),
        # null, not true, when nothing was scored: vacuous truth reads as a pass
        "owned_only": None if not scored_ids
        else (scored_ids <= set(receipt["owned_event_ids"]) and not (scored_ids & set(excluded))),
    }
    return receipt


def persist(result: dict) -> pathlib.Path:
    """Durable receipt. A verdict nobody can reread is not a verdict."""
    receipt = receipt_path(result["target"], result["verdict"])
    receipt.write_text(json.dumps(result, indent=2) + "\n")
    return receipt


def _last_row_id(ledger: pathlib.Path, target: str) -> str | None:
    """The row this one supersedes: the latest row already written for the target."""
    if not ledger.exists():
        return None
    previous = None
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("target") == target:
            previous = row.get("row_id")
    return previous


def log_event(result: dict) -> pathlib.Path:
    """Append the verdict to a durable, append-only ledger.

    Deliberately NOT written back into TrueForge as a synthetic event: the
    auditor must not manufacture provenance inside the store it audits. The
    ledger carries the ids needed to reconstruct the evidence from replay.

    Append-only keeps every row, so rows need identity: `row_id` names this
    row and `supersedes` names the one it replaces, which is how the gate's
    approval row is told apart from the verdict row it followed. Counting
    rows is not counting verdicts -- follow the chain.
    """
    ledger = _receipt_dir() / "verdicts.jsonl"
    row = dict(result)
    row["row_id"] = uuid.uuid4().hex
    row["supersedes"] = _last_row_id(ledger, result["target"])
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return ledger
