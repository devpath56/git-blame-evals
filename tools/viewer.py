"""Read-only viewer over the receipt directory.

Serves the screens and three JSON endpoints. It reads $AUDITOR_RECEIPT_DIR and
NOTHING else: it never writes, and it never calls TrueForge. That is the point
rather than an omission -- the browser half of the demo must stay up when
TrueForge is down, so the UI can never be the thing that breaks the run.

  GET /api/runs            ledger rows, supersedes-collapsed, newest first
  GET /api/runs/{target}   the full receipt for one run
  GET /api/metrics         the two numbers, or UNSCORABLE with the reason

The verdict and approval regions stay separate all the way to the wire. The
viewer never folds `outcome` into `verdict`: a score exists if and only if
`outcome` is non-null, and an approved partial keeps its UNEVALUABLE verdict.
"""
import json
import os
import pathlib
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.verdict import APPROVAL_FIELDS, VERDICT_FIELDS  # noqa: E402

UI_DIR = pathlib.Path(__file__).resolve().parent.parent / "ui"
LEDGER = "verdicts.jsonl"
TARGET_RE = re.compile(r"^[0-9a-zA-Z]{1,64}$")
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".json": "application/json"}

# The north star cannot be computed from a row that never declared how many
# foreign spans were planted. Both fields are required: a recall with no
# denominator is not a measurement, and a denominator with no recall is not
# an answer. Either one missing takes the metric to UNSCORABLE by name.
REQUIRED_FOR_NORTH_STAR = ("planted_foreign_events", "foreign_recall")

# The per-span arrays. Cheap to say, expensive to ship on the list path -- see
# project(). Every one of these is served whole by /api/runs/{target}.
HEAVY = frozenset({"foreign", "unwitnessed", "owned_event_ids",
                   "unwitnessed_event_ids", "registry_sessions"})
LIST_FIELDS = (VERDICT_FIELDS | APPROVAL_FIELDS) - HEAVY


def receipt_dir() -> pathlib.Path:
    directory = os.environ.get("AUDITOR_RECEIPT_DIR", "").strip()
    if not directory:
        raise SystemExit("AUDITOR_RECEIPT_DIR is not set -- see README.md")
    return pathlib.Path(directory)


def read_ledger() -> list[dict]:
    """Every row, in file order, with its sequence kept for tie-breaking."""
    ledger = receipt_dir() / LEDGER
    if not ledger.exists():
        return []
    rows = []
    for seq, line in enumerate(ledger.read_text(encoding="utf-8").splitlines()):
        if line.strip():
            rows.append(dict(json.loads(line), _seq=seq))
    return rows


def live_rows(rows: list[dict]) -> list[dict]:
    """Collapse the supersedes chain: one row per run, the one nothing replaced.

    Computed from the chain rather than from file order, so a ledger that was
    concatenated or re-sorted still collapses to the same set.
    """
    superseded = {row["supersedes"] for row in rows if row.get("supersedes")}
    live = [row for row in rows if row.get("row_id") not in superseded]
    return sorted(live, key=lambda r: (r.get("emitted_at") or "", r["_seq"]), reverse=True)


def project(row: dict) -> dict:
    """The list view's row: DERIVED from the field sets verdict.py declares.

    An earlier version hand-listed ~18 field names here, which gave the receipt
    shape a third home (verdict.py writes it, this re-listed it, the UI read it
    again) and a field added upstream would silently stop reaching the browser.
    Deriving from VERDICT_FIELDS|APPROVAL_FIELDS leaves one owner.

    HEAVY is NOT a copy of the write shape -- it is a statement about the READ.
    /api/runs serves two of three views; `foreign[]` is needed by exactly one of
    them, the run detail, which fetches the whole receipt anyway. Shipping those
    arrays on the list path would make the frequent read pay for the rare one
    (measured: 17.0 kB against 2.5 kB). They are dropped here and nowhere else.
    """
    fields = LIST_FIELDS & row.keys()
    out = {k: row[k] for k in fields}
    # row identity is minted by log_event, so it is in neither declared set.
    out["row_id"] = row.get("row_id")
    out["supersedes"] = row.get("supersedes")
    # outcome stays null when nothing was scored -- a vacuous truth must not
    # read as a pass, so the UI renders an em dash rather than a zero.
    out.setdefault("outcome", None)
    out.setdefault("approval", None)
    return out


def metrics(rows: list[dict]) -> dict:
    """The two numbers, computed inline from the collapsed ledger.

    North star -- evidence ownership verified: runs whose planted foreign spans
    were all detected, over runs audited.
    Counter    -- false refusal: clean runs that did not reach SCORED, over
    clean runs. A gate that cries wolf gets bypassed, so this one must stay 0.
    """
    counted = live_rows(rows)
    missing = [(row.get("target"), field) for row in counted
               for field in REQUIRED_FOR_NORTH_STAR if row.get(field) is None]

    if missing or not counted:
        target, field = missing[0] if missing else (None, None)
        north = {
            "state": "UNSCORABLE",
            "runs": len(counted),
            "why": f"row {target} carries no {field}" if missing
                   else "no runs in the ledger",
        }
    else:
        verified = sum(1 for row in counted if (row.get("foreign_recall") or 0) >= 1.0)
        north = {"state": "SCORED", "verified": verified, "runs": len(counted),
                 "text": f"{verified}/{len(counted)} runs"}

    clean = [row for row in counted
             if (row.get("claimed_events") or 0) > 0
             and (row.get("foreign_events") or 0) == 0
             and (row.get("unwitnessed_events") or 0) == 0]
    refused = [row for row in clean if row.get("verdict") != "SCORED"]
    return {
        "north_star": dict(north, label="evidence ownership verified"),
        "counter": {"label": "false refusal", "false_refusals": len(refused),
                    "clean_runs": len(clean),
                    "text": f"{len(refused)}/{len(clean)} clean runs"},
    }


def find_receipt(target: str) -> dict | None:
    """The receipt on disk for one run. Rejects anything that is not an id."""
    if not TARGET_RE.match(target or ""):
        return None
    matches = sorted(receipt_dir().glob(f"{target}-*.json"))
    if not matches:
        return None
    return json.loads(matches[-1].read_text(encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _send(self, payload, status=200, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (UI_DIR / name).resolve()
        if UI_DIR.resolve() not in target.parents or not target.is_file():
            return self._send({"error": "not found"}, 404)
        ctype = TYPES.get(target.suffix, "application/octet-stream")
        self._send(target.read_bytes(), ctype=ctype)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        # A missing or empty ledger is an empty list, never a 500: a viewer that
        # crashes on cold state is a viewer that cannot open the demo.
        if path == "/api/runs":
            return self._send([project(row) for row in live_rows(read_ledger())])
        if path.startswith("/api/runs/"):
            receipt = find_receipt(path[len("/api/runs/"):])
            return self._send(receipt or {"error": "no receipt for that target"},
                              200 if receipt else 404)
        if path == "/api/metrics":
            return self._send(metrics(read_ledger()))
        return self._static(path)


def main():
    port = os.environ.get("VIEWER_PORT", "").strip()
    if not port:
        raise SystemExit("VIEWER_PORT is not set -- see README.md")
    receipt_dir()  # fail now, with the same message, rather than per request
    # THREADING IS NOT OPTIONAL HERE. protocol_version is HTTP/1.1, so a browser
    # holds its connection open; a single-threaded HTTPServer then serves that
    # one client and every other request hangs -- measured, the pane wedged the
    # server and curl timed out. A second tab, or a terminal curl during the
    # demo, is exactly the case that must not block.
    ThreadingHTTPServer(("127.0.0.1", int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
