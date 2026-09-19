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

# Unattended runs need a decision source. This is a TEST AFFORDANCE, not a
# human decision, and it is recorded as decision_source="auto" in the receipt.
# The real gate is the interactive prompt -- see README.
export AUDITOR_AUTO_DECISION="${AUDITOR_AUTO_DECISION:-deny}"

echo
echo "=== approval gate: PAUSE -- exactly one decision this run ==="
set +e; python3 src/gate.py --target "$TARGET"; gate_exit=$?; set -e
case "$gate_exit" in
  0|3) ;;
  *) echo "gate failed with exit $gate_exit" >&2; exit 1 ;;
esac

echo
echo "demo complete: SCORED, UNEVALUABLE, approval pause (decision=$AUDITOR_AUTO_DECISION)"
