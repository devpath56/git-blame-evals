"""An MCP server exposing ONE approval-gated tool: score_eval.

Scoring is a tool operation precisely so TrueForge's native gate can pause it.
The tool is the enforcement point, not the UI: it scores registry-owned spans
ONLY, reading the excluded set from the UNEVALUABLE receipt. Approval permits
scoring of the owned subset -- it can never launder a foreign span.
"""
import json
import os
import pathlib
from http.server import BaseHTTPRequestHandler, HTTPServer

TOOL = {
    "name": "score_eval",
    "description": "Score an evaluation for a target session. Scores registry-owned spans only.",
    "inputSchema": {
        "type": "object",
        "properties": {"target_session_id": {"type": "string", "description": "Session under evaluation."}},
        "required": ["target_session_id"],
    },
}


def score_eval(target_session_id: str) -> dict:
    """Score owned spans only. Foreign spans are excluded, never scored."""
    directory = os.environ.get("AUDITOR_RECEIPT_DIR", "").strip()
    receipt_path = pathlib.Path(directory) / f"{target_session_id}-unevaluable.json"
    if not receipt_path.exists():
        return {"error": f"no UNEVALUABLE receipt for {target_session_id}"}
    receipt = json.loads(receipt_path.read_text())
    excluded = [span["event_id"] for span in receipt["foreign"]]
    owned = list(receipt["owned_event_ids"])
    return {
        "target_session_id": target_session_id,
        "scored_event_ids": owned,
        "scored_span_count": len(owned),
        "excluded_span_count": len(excluded),
        "excluded_event_ids": excluded,
        "score": round(len(owned) / max(receipt["claimed_events"], 1), 4),
        "note": "owned-only; the judge model call is out of scope for this step",
    }


def dispatch(message: dict):
    """Minimal JSON-RPC surface: initialize, tools/list, tools/call."""
    method, request_id = message.get("method"), message.get("id")
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion") or "2025-06-18"
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "eval-grain-scorer", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        params = message.get("params") or {}
        payload = score_eval((params.get("arguments") or {}).get("target_session_id", ""))
        result = {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": "error" in payload}
    elif request_id is None:
        return None
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        message = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        response = dispatch(message)
        if response is None:
            self.send_response(202)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        body = json.dumps(response).encode()
        accepts_sse = "text/event-stream" in (self.headers.get("accept") or "")
        self.send_response(200)
        if accepts_sse:
            self.send_header("content-type", "text/event-stream")
            body = b"event: message\ndata: " + body + b"\n\n"
        else:
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = os.environ.get("SCORE_MCP_PORT", "").strip()
    if not port:
        raise SystemExit("SCORE_MCP_PORT is not set -- see README.md")
    HTTPServer(("127.0.0.1", int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
