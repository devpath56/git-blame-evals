# TrueForge eval-grain auditor — Step 1 (clean path)

Verifies that every span an eval scored actually belongs to the run being scored.

The registry is a dict keyed by `(session_id, turn_id, event_id)`.
The audit is a membership test against that dict. That is the whole mechanism.

Ownership and the claim come from two **independent** TrueForge views, so the
audit cannot pass by checking a view against itself:

| what | source |
|---|---|
| ownership truth | the **live SSE stream** (`POST /turns` with `stream=true`) |
| the eval's claim | **replay** (`GET /sessions/{id}/events`), grouped by event id |

Grain note: `event_id` is read off the event body. `session_id` and `turn_id` are
ingest context — the stream the event arrived on. Only `turn.created` carries
`turn_id` on the wire. Ownership is provenance, which is exactly why an eval that
groups by a human-readable label can attribute a span to the wrong run.

## Scope

Step 1 is the clean happy path only: live ingest, registry, and the **SCORED**
receipt. Contaminated fixtures, the UNEVALUABLE demo and the approval gate are
Step 2 and are deliberately not built here.

## Prerequisites

TrueForge must already be running. No host or port is hardcoded in `src/` —
every one arrives by environment variable.

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

```bash
python3 tools/stub_model.py > /tmp/stub.log 2>&1 &
sleep 1
curl -sf -m 5 "$STUB_MODEL_URL/models" > /dev/null && echo "stub model up"
```

## Produce the SCORED receipt

```bash
SESSION_ID=$(python3 tools/provision.py clean-eval)
python3 src/audit.py --session "$SESSION_ID"
```

Expected output:

```text
SCORED
  target: <session id>
  claimed_events: 3
  owned_events: 3
  foreign_events: 0
  evidence_source: TrueForge replay
```

Exit code is `0` for SCORED and `2` for UNEVALUABLE. The durable receipt lands
in `$AUDITOR_RECEIPT_DIR`.

## Files

| file | non-blank LOC | role |
|---|---|---|
| `src/registry.py` | 56 | ownership registry + membership test |
| `src/stream.py` | 70 | TrueForge transport, SSE parser, replay |
| `src/verdict.py` | 63 | verdict contract, rendering, durable receipt |
| `src/audit.py` | 53 | orchestration: live ingest → claim → verdict |
| `tools/stub_model.py` | 59 | labeled fallback model endpoint |
| `tools/provision.py` | 33 | provider + session provisioning |
