"""TrueForge transport: JSON calls and live SSE ingest.

No host or port literal appears in this package. TRUEFORGE_BASE_URL is
required; an unset value is an error, never a guessed default.
"""
import json
import os
import urllib.error
import urllib.request

TIMEOUT = 120


def base_url() -> str:
    url = os.environ.get("TRUEFORGE_BASE_URL", "").strip()
    if not url:
        raise SystemExit("TRUEFORGE_BASE_URL is not set -- see README.md")
    return url.rstrip("/")


def _open(method: str, path: str, payload=None, sse: bool = False):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(base_url() + path, data=body, method=method)
    request.add_header("content-type", "application/json")
    request.add_header("accept", "text/event-stream" if sse else "application/json")
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:
        detail = error.read().decode()[:400]
        raise SystemExit(f"{method} {path} -> {error.code}: {detail}") from None


def api(method: str, path: str, payload=None) -> dict:
    with _open(method, path, payload) as response:
        raw = response.read().decode()
    return json.loads(raw) if raw.strip() else {}


def iter_sse(response):
    """Yield each parsed SSE data frame from an open event-stream response."""
    buffer: list[str] = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith(":"):
            continue
        if line:
            if line.startswith("data:"):
                buffer.append(line[5:].lstrip())
            continue
        frame, buffer = "\n".join(buffer), []
        parsed = _parse(frame)
        if parsed is not None:
            yield parsed
    parsed = _parse("\n".join(buffer))
    if parsed is not None:
        yield parsed


def _parse(frame: str):
    frame = frame.strip()
    if not frame or frame == "[DONE]":
        return None
    try:
        return json.loads(frame)
    except json.JSONDecodeError:
        return None


def stream_turn(session_id: str, turn_input: list[dict]):
    """Start a turn and return its LIVE SSE response (stream=true)."""
    payload = {"input": turn_input, "stream": True}
    return _open("POST", f"/api/v1/sessions/{session_id}/turns", payload, sse=True)


def replay_session_events(session_id: str) -> list[dict]:
    """Replay: the evidence an eval reads back. Returns SessionEventItem rows."""
    rows, token = [], None
    while True:
        path = f"/api/v1/sessions/{session_id}/events?limit=100"
        if token:
            path += f"&page_token={token}"
        page = api("GET", path)
        rows.extend(page.get("data", []))
        token = (page.get("pagination") or {}).get("next_page_token")
        if not token:
            return rows
