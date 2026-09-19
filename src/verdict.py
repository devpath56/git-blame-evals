"""The verdict contract. Exactly one terminal verdict per audit."""
import json
import os
import pathlib
import time

SCORED = "SCORED"
UNEVALUABLE = "UNEVALUABLE"


def audit(registry, target_session: str, claimed: list[dict]) -> dict:
    """Membership-test every claimed span against the live ownership registry.

    `claimed` carries what the eval says it scored: {turn_id, event_id}.
    """
    owned, foreign = [], []
    for span in claimed:
        turn_id, event_id = span.get("turn_id"), span.get("event_id")
        if registry.owns(target_session, turn_id, event_id):
            owned.append(span)
            continue
        true_owners = registry.resolve(event_id)
        foreign.append(
            {
                "label": span.get("label"),
                "event_id": event_id,
                "claimed_turn_id": turn_id,
                "true_session_id": true_owners[0].session_id if true_owners else None,
                "true_turn_id": true_owners[0].turn_id if true_owners else None,
            }
        )
    return {
        "verdict": SCORED if not foreign else UNEVALUABLE,
        "target": target_session,
        "claimed_events": len(claimed),
        "owned_events": len(owned),
        "foreign_events": len(foreign),
        "foreign": foreign,
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
    ]
    if result["verdict"] == UNEVALUABLE:
        lines.append("  foreign:")
        lines.extend(f"    {json.dumps(span)}" for span in result["foreign"])
        lines.append("  next_state: TrueForge approval required")
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


def persist(result: dict) -> pathlib.Path:
    """Durable receipt. A verdict nobody can reread is not a verdict."""
    receipt = _receipt_dir() / f"{result['target']}-{result['verdict'].lower()}.json"
    receipt.write_text(json.dumps(result, indent=2) + "\n")
    return receipt


def log_event(result: dict) -> pathlib.Path:
    """Append the verdict to a durable, append-only ledger.

    Deliberately NOT written back into TrueForge as a synthetic event: the
    auditor must not manufacture provenance inside the store it audits. The
    ledger carries the ids needed to reconstruct the evidence from replay.
    """
    ledger = _receipt_dir() / "verdicts.jsonl"
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True) + "\n")
    return ledger
