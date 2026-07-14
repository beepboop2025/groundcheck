"""Streamable-HTTP MCP transport for the hosted engine.

The npm package (groundcheck-mcp) is a stdio server an agent installs. This is
the other half: an MCP endpoint an agent adds *by URL*, no install — which is
what remote clients (Claude connectors, ChatGPT, Cursor) and gateways
(Smithery, Glama) speak.

Same ethos as the rest of the engine: stdlib JSON-RPC 2.0, no new dependency,
fail loud, one source of truth — every tool calls the same functions the REST
endpoints do.

Surface and payment mirror the REST side exactly:
  verify_claim       free (rate limited)
  check_citations    x402-priced (same price as POST /check)
  resolve_instrument x402-priced (same price as POST /resolve)
Unpaid calls to a priced tool answer HTTP 402 with the offer, so a
wallet-holding agent can pay and retry. Free daily quota applies first, exactly
like the REST paths.
"""
from __future__ import annotations

from typing import Any, Callable, Awaitable

from . import config, x402

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "groundcheck"
SERVER_VERSION = "0.5.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

MAX_BATCH = 8

# tool name -> the REST path whose price and free-quota it shares. A tool absent
# from this map is free.
TOOL_PRICED_AS = {
    "check_citations": "/check",
    "resolve_instrument": "/resolve",
}

TOOLS: list[dict] = [
    {
        "name": "verify_claim",
        "description": (
            "Verify a single factual claim against live sources. Call this BEFORE "
            "asserting any fact you are not certain of. Returns a verdict "
            "(supported/refuted/unverified), a confidence score, and citations. "
            "Explicit security references in the claim ($AAPL, an ISIN, a FIGI) are "
            "resolved to canonical instruments. Free."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "claim": {"type": "string",
                          "description": "The factual claim, as one complete sentence."},
                "max_sources": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            "required": ["claim"],
        },
    },
    {
        "name": "check_citations",
        "description": (
            "Extract the factual claims from a block of text and verify each one. "
            "Use on AI-generated drafts before publishing. Returns a per-claim "
            "verdict report. Paid per call (x402)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "Text whose factual claims should be checked."},
                "max_claims": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            },
            "required": ["text"],
        },
    },
    {
        "name": "resolve_instrument",
        "description": (
            "Resolve a security identifier (ticker, ISIN, CUSIP, SEDOL, FIGI) or an "
            "instrument name to canonical FIGI records via Bloomberg open symbology, "
            "with provenance attached. Call this before acting on a claim, order, or "
            "document that names a security, so you know exactly WHICH instrument it "
            "is about. Paid per call (x402)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Ticker, ISIN, CUSIP, SEDOL, FIGI, or instrument name."},
                "id_type": {"type": "string",
                            "enum": ["TICKER", "ID_ISIN", "ID_CUSIP", "ID_SEDOL", "ID_BB_GLOBAL"],
                            "description": "Optional; auto-detected from the value's shape."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            "required": ["query"],
        },
    },
]


def _result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _text_result(msg_id: Any, payload: Any, is_error: bool = False) -> dict:
    import json as _json
    return _result(msg_id, {
        "content": [{"type": "text", "text": _json.dumps(payload, indent=2, default=str)}],
        "isError": is_error,
    })


def priced_tool(msg: Any) -> str | None:
    """The REST path a single tools/call message is priced as, or None if the
    message is not a paid tool call."""
    if not isinstance(msg, dict) or msg.get("method") != "tools/call":
        return None
    name = (msg.get("params") or {}).get("name")
    return TOOL_PRICED_AS.get(name)


def tool_name(msg: Any) -> str | None:
    if not isinstance(msg, dict) or msg.get("method") != "tools/call":
        return None
    return (msg.get("params") or {}).get("name")


def annotate_tools_list(resp: dict) -> dict:
    """Tell wallet-holding agents which tools cost money, and how much."""
    tools = (resp.get("result") or {}).get("tools")
    if not isinstance(tools, list):
        return resp
    for t in tools:
        path = TOOL_PRICED_AS.get(t.get("name"))
        price = x402.price_usd(path) if path else None
        if price is not None:
            t["_meta"] = {
                "x402": {
                    "price": {"mode": "fixed", "currency": "USD", "amount": f"{price:.6f}"},
                    "payTo": x402.pay_to_address(),
                    "note": "unpaid calls answer HTTP 402 with the offer",
                }
            }
    return resp


def _handle_initialize(msg_id: Any, params: dict) -> dict:
    requested = params.get("protocolVersion")
    return _result(msg_id, {
        "protocolVersion": requested if isinstance(requested, str) else PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": (
            "Ground claims before asserting them (verify_claim), verify a whole draft "
            "(check_citations), and resolve which security a claim is about "
            "(resolve_instrument). Paid tools answer 402 with an x402 offer. "
            "Sibling servers from the same lab: for US money-market stress readings "
            "use Seiche at https://api.seiche.info/mcp; for bank and lender failure "
            "risk (Indian institutions live, plus the US and European failure "
            "records) use LiquiLens at https://api.liquilens.in/mcp; for internet "
            "censorship and information-control signals use Palimpsest at "
            "https://api.seiche.info/palimpsest/mcp."
        ),
    })


async def dispatch(msg: Any, handlers: dict[str, Callable[..., Awaitable[Any]]]) -> dict | None:
    """Route one JSON-RPC message. None for notifications (no reply)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(msg.get("id") if isinstance(msg, dict) else None,
                      INVALID_REQUEST, "not a JSON-RPC 2.0 message")
    if "id" not in msg:                       # notification
        return None

    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        return _handle_initialize(msg_id, params)
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        import copy
        return _result(msg_id, {"tools": copy.deepcopy(TOOLS)})
    if method == "resources/list":
        return _result(msg_id, {"resources": []})
    if method == "prompts/list":
        return _result(msg_id, {"prompts": []})
    if method != "tools/call":
        return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")

    name = params.get("name")
    args = params.get("arguments")
    if not isinstance(args, dict):
        args = {}
    handler = handlers.get(name)
    if handler is None:
        return _error(msg_id, INVALID_PARAMS, f"unknown tool: {name}")
    try:
        payload = await handler(**args)
    except TypeError as exc:                  # bad/missing arguments
        return _error(msg_id, INVALID_PARAMS, str(exc))
    except Exception as exc:                  # noqa: BLE001 — never 500 the transport
        return _text_result(msg_id, {"error": f"{type(exc).__name__}: {exc}"}, is_error=True)
    return _text_result(msg_id, payload)
