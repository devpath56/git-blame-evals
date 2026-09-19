"""How an eval decides WHICH spans it scored -- the grain, and the bug.

Two grouping strategies over the same replay rows:

  claim_by_event_id  correct. The event id is globally unique, so a span can
                     only ever resolve to the run that actually produced it.

  claim_by_label     CF-262. Group by a human-readable label ("LLM call 9").
                     That label is unique only WITHIN a session, so reusing it
                     across sessions silently merges three runs' spans into
                     one claim. Nothing inside the eval can notice: every span
                     it collected is real, well-formed, and individually valid.
"""
LABEL = "LLM call {n}"
SCORED_EVENT_TYPES = ("model.message",)


def label_spans(session_id: str, rows: list[dict]) -> list[dict]:
    """Label each scored model call, numbered within its own session."""
    spans, count = [], 0
    for row in sorted(rows, key=lambda r: (r["event"].get("created_at") or "")):
        event = row.get("event") or {}
        if event.get("type") not in SCORED_EVENT_TYPES or not event.get("id"):
            continue
        count += 1
        spans.append(
            {
                "label": LABEL.format(n=count),
                "session_id": session_id,
                "turn_id": row["turn_id"],
                "event_id": event["id"],
            }
        )
    return spans


def claim_by_event_id(spans: list[dict]) -> list[dict]:
    """The clean judge: claim exactly the spans of the run under evaluation."""
    return [{"turn_id": s["turn_id"], "event_id": s["event_id"], "label": s["label"]} for s in spans]


def claim_by_label(target_session: str, spans_by_session: dict[str, list[dict]]) -> list[dict]:
    """The contaminated judge: collect every span sharing the target's labels.

    `_fixture_owner` is bookkeeping for recall accounting only. The audit never
    reads it -- true owners are resolved from the registry, not from the claim.
    """
    by_label: dict[str, list[dict]] = {}
    for spans in spans_by_session.values():
        for span in spans:
            by_label.setdefault(span["label"], []).append(span)

    claimed, seen = [], set()
    for span in spans_by_session.get(target_session, []):
        for candidate in by_label.get(span["label"], []):
            key = (candidate["session_id"], candidate["event_id"])
            if key in seen:
                continue
            seen.add(key)
            claimed.append(
                {
                    "turn_id": candidate["turn_id"],
                    "event_id": candidate["event_id"],
                    "label": candidate["label"],
                    "_fixture_owner": candidate["session_id"],
                }
            )
    return claimed
