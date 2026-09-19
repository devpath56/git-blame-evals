"""Provision TrueForge: register the model provider, an agent, and a session."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import stream  # noqa: E402

PROVIDER, MODEL = "stub", "stub-model"
MCP_SERVER = "eval-scorer"


def ensure_mcp_server() -> None:
    """Register the approval-gated scoring tool as a remote MCP server."""
    url = os.environ.get("SCORE_MCP_URL", "").strip()
    if not url:
        raise SystemExit("SCORE_MCP_URL is not set -- see README.md")
    configured = stream.api("GET", "/api/v1/settings/mcp-servers").get("data", [])
    if any(server.get("name") == MCP_SERVER for server in configured):
        return
    manifest = {
        "type": "remote",
        "name": MCP_SERVER,
        "url": url,
        "description": "Approval-gated eval scoring for the eval-grain auditor",
    }
    stream.api("POST", "/api/v1/settings/mcp-servers", {"manifest": manifest})


def ensure_provider() -> None:
    url = os.environ.get("STUB_MODEL_URL", "").strip()
    if not url:
        raise SystemExit("STUB_MODEL_URL is not set -- see README.md")
    configured = stream.api("GET", "/api/v1/settings/model-providers").get("data", [])
    if any(provider.get("name") == PROVIDER for provider in configured):
        return
    manifest = {
        "type": "custom",
        "name": PROVIDER,
        "base_url": url,
        "models": [{"model_id": MODEL, "name": MODEL, "properties": {"context_length": 8192}}],
    }
    stream.api("POST", "/api/v1/settings/model-providers", {"manifest": manifest})


def ensure_session(label: str) -> str:
    spec = {
        "model": {"name": f"{PROVIDER}/{MODEL}"},
        "instructions": "Answer in one short sentence.",
    }
    session = stream.api("POST", "/api/v1/sessions", {"agent": {"spec": spec}, "metadata": {"name": label}})
    return (session.get("data") or session)["id"]


def main() -> None:
    ensure_provider()
    if "--register-mcp" in sys.argv:
        ensure_mcp_server()
        return
    label = sys.argv[1] if len(sys.argv) > 1 else "clean-eval"
    print(ensure_session(label))


if __name__ == "__main__":
    main()
