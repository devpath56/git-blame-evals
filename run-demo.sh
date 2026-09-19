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
echo
echo "demo complete: SCORED then UNEVALUABLE"
