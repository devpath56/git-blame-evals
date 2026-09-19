# TrueForge eval-grain auditor

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

### Three answers, not two

A claimed span the target does not own splits two ways, because they indict
different parties:

| verdict | means | a fact about |
|---|---|---|
| `SCORED` | every claimed span is owned by the target | the eval |
| `UNEVALUABLE` | at least one span is held under **another session** | the **eval** |
| `UNWITNESSED` | no span is foreign, but some are **absent from the registry** | the **auditor** |

An empty claim is `UNEVALUABLE` with `reason: "empty claim: nothing to verify"` —
zero foreign out of zero claimed is not a pass.

`foreign` wins over `unwitnessed` when both are present; the unwitnessed ids are
still listed under `unwitnessed_event_ids`. Unwitnessed spans carry a null owner
by construction: the registry never saw them, so there is nobody to name.

## Scope

Steps 1–3: live ingest, registry, both verdicts, durable ledger, and the native
approval gate. The judge model call is a labeled stub — see **LABELED FALLBACK**.

## The approval gate (Step 3)

`UNEVALUABLE` routes to **TrueForge's own** tool-approval flow, not to a prompt
this project invented. Scoring is implemented as an MCP tool so TrueForge can
pause it:

1. `score_eval` is served by `tools/score_mcp.py` and registered as a remote MCP
   server. TrueForge discovers it through its own MCP client.
2. The agent attaches it with `require_approval_for_tools: ["score_eval"]`.
3. On the call, TrueForge emits `tool.approval_required` and **pauses the turn**.
4. The human sees the finding — N foreign spans with their true owner IDs — and
   **types the decision**. The resume is a `user.tool_approval` turn input
   carrying `{status: "allow" | "deny"}`.

### The interactive pause IS the gate

`src/gate.py` never decides on its own. It prompts and blocks:

```text
  approve / reject > reject

>>> decision: DENY  (source: human)
```

With no TTY and no flag it **refuses to proceed** rather than choosing for you:

```text
no TTY and AUDITOR_AUTO_DECISION is unset -- refusing to decide for you.
```

`AUDITOR_AUTO_DECISION=allow|deny` is a **test affordance** for timed and
unattended runs only. It is not a human decision and is recorded as
`decision_source: "auto"` in the receipt, against `"human"` for a typed answer.
The fenced demo block below sets it so the README stays executable unattended.

**Pause wait time never counts against the 10-minute cold-run budget.** The
clock covers reaching both verdicts and the pause; how long a human deliberates
is not the tool's latency.

### One decision per receipt

Exactly one decision is recorded per run, written to the verdict receipt and the
ledger from the same value that was displayed — they cannot diverge. Gating a
receipt that already carries a decision is refused:

```text
already decided: deny at 2026-09-19T21:15:03Z -- refusing to decide twice on one receipt.
```

**The gate never launders foreign evidence.** Enforcement lives in the tool, not
the UI: `score_eval` scores `owned_event_ids` only. Approval permits scoring of
the owned subset; it cannot widen the set. The receipt records the decision, the
excluded ids, the scored ids, and `owned_only`.

| decision | what happens | receipt | exit |
|---|---|---|---|
| **approve** | owned spans scored, foreign excluded | `scored_event_ids` = owned, `foreign_spans_scored: []`, `owned_only: true` | 0 |
| **reject** | TrueForge returns `User denied tool call: <reason>`; the tool never executes | `scored_event_ids: []`, `owned_only: null` | 3 |

`owned_only` is `null`, not `true`, when nothing was scored — a vacuous truth
must not read as a pass.

Interactive (the real gate — you type the answer):

```text
python3 src/gate.py --target "$TARGET"
  approve / reject > approve      # exit 0
  approve / reject > reject       # exit 3
```

Unattended form in **Or each verdict on its own**, below.

## Plugin interface (for non-TrueForge harnesses)

TrueForge is the **reference adapter**, not a dependency of the idea. An adapter
for another harness (DeepSeek harness, Mastra, Agno, VoltAgent) implements three
functions. No runtime code in this repo depends on these signatures yet; this is
the contract a future adapter should be written against.

```python
def ingest_stream(target_id: str, registry: OwnershipRegistry) -> tuple[str, int]:
    """Consume the harness's LIVE event stream into the ownership registry.

    Must attribute each event to (session_id, turn_id, event_id) from INGEST
    CONTEXT -- the stream it arrived on -- never from a label in the payload.
    Returns (turn_id, frames_seen).
    Reference: src/stream.py:ingest_live_turn
    """

def read_claim(target_id: str) -> list[dict]:
    """Return what the eval says it scored, from a source INDEPENDENT of the
    live stream (replay, a log, an export). Each span: {turn_id, event_id}.
    Independence is what stops the audit checking a view against itself.
    Reference: src/stream.py:replay_session_events + src/harness.py
    """

def gate(receipt: dict, owned_event_ids: list[str]) -> dict:
    """Present the UNEVALUABLE finding to a human and block until decided.

    Must return {"decision": "allow"|"deny", "scored_event_ids": [...]}.
    Contract: scored_event_ids MUST be a subset of owned_event_ids on allow,
    and empty on deny. An adapter that cannot block is not a gate -- report the
    limitation rather than scoring unapproved.
    Reference: src/gate.py
    """
```

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
export SCORE_MCP_PORT=8792
export SCORE_MCP_URL=http://localhost:8792/
# Optional, unattended runs only: allow | deny. Unset = interactive prompt.
# export AUDITOR_AUTO_DECISION=deny
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
python3 tools/score_mcp.py > /tmp/mcp.log 2>&1 &
sleep 1
curl -sf -m 5 "$STUB_MODEL_URL/models" > /dev/null && echo "stub model up"
python3 tools/provision.py --register-mcp && echo "scoring tool registered"
```

The stub also **decides to call the gated tool**, deterministically, on every
first pass. A real model chooses; this one always calls. Everything downstream
— the pause, the human decision, the denial — is real TrueForge.

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
  unwitnessed_events: 0
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
  unwitnessed_events: 0
  foreign:
    {"label": "LLM call 9", "event_id": "...", "claimed_turn_id": "...",
     "true_session_id": "...", "true_turn_id": "..."}
  next_state: TrueForge approval required
```

Then route that UNEVALUABLE verdict through the gate. Drop
`AUDITOR_AUTO_DECISION=` to be prompted instead — that is the real gate:

```bash
TARGET=$(ls -t "$AUDITOR_RECEIPT_DIR"/*-unevaluable.json | head -1 | xargs basename | sed 's/-unevaluable.json//')
AUDITOR_AUTO_DECISION=deny python3 src/gate.py --target "$TARGET" || [ $? -eq 3 ]
```

Exit code is `0` for SCORED and `2` for any other verdict. Each verdict writes a
JSON receipt and appends to the durable ledger `$AUDITOR_RECEIPT_DIR/verdicts.jsonl`.

Ledger rows carry `row_id` and `supersedes`. Append-only keeps every row, so the
gate's approval row does not replace the verdict row — it supersedes it by id.
**Counting rows is not counting verdicts; follow the chain.**

The ledger is deliberately **not** written back into TrueForge as synthetic
events: an auditor must not manufacture provenance inside the store it audits.
It records the ids needed to reconstruct the evidence from replay.

## Files

| file | non-blank LOC | role |
|---|---|---|
| `src/gate.py` | 118 | interactive approval gate, decision integrity |
| `src/verdict.py` | 103 | verdict contract, receipt, ledger, approval record |
| `tools/score_mcp.py` | 88 | MCP server: the approval-gated `score_eval` tool |
| `src/stream.py` | 86 | transport, SSE parser, live ingest, replay |
| `tools/stub_model.py` | 81 | labeled fallback model + stubbed tool-call decision |
| `src/audit.py` | 57 | orchestration: clean and contaminated modes |
| `src/harness.py` | 57 | the two grouping strategies (event id vs label) |
| `src/registry.py` | 56 | ownership registry + membership test |
| `tools/provision.py` | 52 | provider, session and MCP provisioning |
| `run-demo.sh` | 42 | all three beats, one command |
| `tools/fixture.py` | 28 | deterministic CF-262 contamination fixture |
| **total** | **768** | |
