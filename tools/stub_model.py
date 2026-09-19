"""LABELED FALLBACK -- a deterministic OpenAI-compatible model endpoint.

This stubs ONLY the model's text. Every TrueForge object the auditor reads
-- sessions, turns, events, the SSE stream, replay -- is real. Swap this for
a real OpenAI provider by pointing the provider at api.openai.com instead.
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

REPLY = "Ownership is provenance. This reply is stubbed; the trace around it is not."


def _completion(model: str) -> dict:
    return {
        "id": f"chatcmpl-stub-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": REPLY},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 32, "completion_tokens": 18, "total_tokens": 50},
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _send(self, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"object": "list", "data": [{"id": "stub-model", "object": "model"}]})

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        payload = _completion(request.get("model", "stub-model"))
        if not request.get("stream"):
            return self._send(payload)
        chunk = dict(payload, object="chat.completion.chunk")
        chunk["choices"] = [{"index": 0, "delta": {"role": "assistant", "content": REPLY}, "finish_reason": "stop"}]
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())
        self.close_connection = True


def main():
    port = os.environ.get("STUB_MODEL_PORT", "").strip()
    if not port:
        raise SystemExit("STUB_MODEL_PORT is not set -- see README.md")
    HTTPServer(("127.0.0.1", int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
