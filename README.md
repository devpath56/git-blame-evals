# TrueForge eval-grain auditor — Steps 1–2

Verifies that every span an eval scored actually belongs to the run being scored.

The registry is a dict keyed by `(session_id, turn_id, event_id)`.
The audit is a membership test against that dict. That is the whole mechanism.

Ownership and the claim come from two **independent** TrueForge views, so the
audit cannot pass by checking a view against itself:

| what | source |
|---|---|
| ownership truth | the **live SSE stream** (`POST /turns` with `stream=true`) |
| the eval's claim | **replay** (`GET /sessions/{id}/events`) |

Grain note: `event_id` is read off the event body. `session_id` and `turn_id` are
ingest context — the stream the event arrived on. Only `turn.created` carries
`turn_id` on the wire. Ownership is provenance, which is exactly why an eval that
groups by a human-readable label can attribute a span to the wrong run.

## The failure this reproduces (CF-262)

A label like `LLM call 9` is unique only **within** a session. Reuse it as a
grouping key across sessions and three runs' spans merge into one claim. Every
span the eval collected is real, well-formed and individually valid — nothing
inside the eval can notice. Only provenance can.

| grouping key | verdict |
|---|---|
| event id (`--session`) | **SCORED** — exit 0 |
| label (`--contaminated`) | **UNEVALUABLE** — exit 2, every foreign span named |

## Scope

Steps 1–2: live ingest, registry, both verdicts, durable ledger. **Step 3 — the
TrueForge approval gate that must pause on UNEVALUABLE — is not built here.**
`next_state: TrueForge approval required` is currently a declared next state, not
an enforced pause.

## Prerequisites

TrueForge must already be running. No host or port is hardcoded in `src/` —
every one arrives by environment variable.

Export these in your **foreground** shell. They must be set before anything is
backgrounded, or the background subshell gets them and your shell does not.

```bash
export TRUEFORGE_BASE_URL=http://localhost:8790
export STUB_MODEL_PORT=8791
export STUB_MODEL_URL=http://localhost:8791/v1
export AUDITOR_RECEIPT_DIR=./receipts
curl -sf -m 5 "$TRUEFORGE_BASE_URL/api/v1/sessions" > /dev/null && echo "TrueForge reachable"
```

## Start the model endpoint

**LABELED FALLBACK.** `tools/stub_model.py` stubs *only the model's text*. Every
TrueForge object the auditor reads — sessions, turns, events, the SSE stream,
replay — is real. To use a real OpenAI key instead, register an `openai`
provider with `auth.api_key` in place of this `custom` provider; nothing in
`src/` changes.

Only the stub is backgrounded; it inherits the environment exported above.

```bash
python3 tools/stub_model.py > /tmp/stub.log 2>&1 &
sleep 1
curl -sf -m 5 "$STUB_MODEL_URL/models" > /dev/null && echo "stub model up"
```

## Both verdicts, one command

```bash
./run-demo.sh
```

## Or each verdict on its own

Clean — the judge groups by event id:

```bash
SESSION_ID=$(python3 tools/provision.py clean-eval)
python3 src/audit.py --session "$SESSION_ID"
```

```text
SCORED
  target: <session id>
  claimed_events: 1
  owned_events: 1
  foreign_events: 0
  evidence_source: TrueForge replay
```

Contaminated — the judge groups by label across three sessions:

```bash
python3 src/audit.py --contaminated --turns 9 || [ $? -eq 2 ]
```

```text
UNEVALUABLE
  target: <session id>
  claimed_events: 27
  owned_events: 9
  foreign_events: 18
  foreign:
    {"label": "LLM call 9", "event_id": "...", "claimed_turn_id": "...",
     "true_session_id": "...", "true_turn_id": "..."}
  next_state: TrueForge approval required
```

Exit code is `0` for SCORED and `2` for UNEVALUABLE. Each verdict writes a JSON
receipt and appends to the durable ledger `$AUDITOR_RECEIPT_DIR/verdicts.jsonl`.

The ledger is deliberately **not** written back into TrueForge as synthetic
events: an auditor must not manufacture provenance inside the store it audits.
It records the ids needed to reconstruct the evidence from replay.

## Files

| file | non-blank LOC | role |
|---|---|---|
| `src/stream.py` | 84 | transport, SSE parser, live ingest, replay |
| `src/verdict.py` | 77 | verdict contract, rendering, receipt, ledger |
| `src/audit.py` | 57 | orchestration: clean and contaminated modes |
| `src/harness.py` | 57 | the two grouping strategies (event id vs label) |
| `src/registry.py` | 56 | ownership registry + membership test |
| `tools/stub_model.py` | 59 | labeled fallback model endpoint |
| `tools/provision.py` | 33 | provider + session provisioning |
| `tools/fixture.py` | 28 | deterministic CF-262 contamination fixture |
| `run-demo.sh` | 25 | both verdicts, one command |
| **total** | **476** | |
