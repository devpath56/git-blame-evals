#!/usr/bin/env bash
# Frozen payoff: one command, both verdicts.
# Env vars must already be exported in the CALLING shell -- see README.md.
set -euo pipefail
for var in TRUEFORGE_BASE_URL STUB_MODEL_PORT STUB_MODEL_URL AUDITOR_RECEIPT_DIR; do
  [ -n "${!var:-}" ] || { echo "$var is not set -- see README.md" >&2; exit 1; }
done
cd "$(dirname "$0")"

# The stub inherits the already-exported environment; only IT is backgrounded.
if ! pgrep -f tools/stub_model.py > /dev/null; then
  python3 tools/stub_model.py > /tmp/stub.log 2>&1 &
  sleep 1
fi
if ! pgrep -f tools/score_mcp.py > /dev/null; then
  python3 tools/score_mcp.py > /tmp/mcp.log 2>&1 &
  sleep 1
fi
python3 tools/provision.py --register-mcp > /dev/null

echo "=== clean eval: judge groups spans by EVENT ID ==="
SESSION_ID="$(python3 tools/provision.py clean-eval)"
python3 src/audit.py --session "$SESSION_ID"

echo
echo "=== contaminated eval: judge groups spans by LABEL (CF-262) ==="
set +e
python3 src/audit.py --contaminated
contaminated_exit=$?
set -e
[ "$contaminated_exit" -eq 2 ] || { echo "expected UNEVALUABLE (exit 2), got $contaminated_exit" >&2; exit 1; }
TARGET=$(ls -t "$AUDITOR_RECEIPT_DIR"/*-unevaluable.json | head -1 | xargs basename | sed 's/-unevaluable.json//')

echo
echo "=== approval gate: REJECT -- nothing scores ==="
set +e; python3 src/gate.py --target "$TARGET" --reject; reject_exit=$?; set -e
[ "$reject_exit" -eq 3 ] || { echo "expected reject exit 3, got $reject_exit" >&2; exit 1; }

echo
echo "=== approval gate: APPROVE -- owned spans only ==="
python3 src/gate.py --target "$TARGET" --approve

echo
echo "demo complete: SCORED, UNEVALUABLE, gate rejected, gate approved (owned-only)"
