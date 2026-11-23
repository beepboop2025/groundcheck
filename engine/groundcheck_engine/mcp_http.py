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
  extract_claims     x402-priced (same price as POST /extract)
  attest_delivery    x402-priced (same price as POST /attest-delivery)
Unpaid calls to a priced tool answer HTTP 402 with the offer, so a
wallet-holding agent can pay and retry. Free daily quota applies first, exactly
like the REST paths.
"""
from __future__ import annotations

from typing import Any, Callable, Awaitable

from . import config, x402

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "groundcheck"
SERVER_VERSION = "0.7.2"

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
    "extract_claims": "/extract",
    "attest_delivery": "/attest-delivery",
}

# What a directory scanner reads is only this advertised surface, never the
# code, so it follows the fleet metadata rules (tests/test_metadata_hygiene.py):
# each tool describes only itself, no URLs, descriptions under 1,000
# characters, and every free-text input carries a length bound. The bounds
# match the limits the REST models already enforce (200 for a query, 500 for a
# service, 10,000 and 16,384 for the optional delivery fields, 100,000 for a
# document); a claim is one sentence, so 10,000 characters leaves ample room.
TOOLS: list[dict] = [
    {
        "name": "verify_claim",
        "description": (
            "Fact-check one claim against live retrieved sources. Returns verdict "
            "(supported, refuted or unverified), sufficiency, a conformal guarantee, "
            "per-part atoms and a signed provenance receipt binding the evidence and "
            "model route. "
            "Sufficiency other than sufficient (insufficient means one weak source; "
            "no_sources, no_stance, conflict) is a reason to abstain or escalate. When "
            "guarantee.certified is true, the error probability is calibrated to at most "
            "guarantee.alpha, distribution-free, so supported plus certified can gate a "
            "decision. Compound claims are verified part by part and recombined "
            "weakest-link, so a true part cannot carry a false one. Limits: evidence is "
            "retrievable web, encyclopedic and news sources, so very recent, private, "
            "niche-technical or opinion claims return unverified or insufficient rather "
            "than a guess. The guarantee exists only on calibrated deployments, for "
            "claims exchangeable with the calibration set. It checks source support, not "
            "ultimate truth. Free."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "claim": {"type": "string", "maxLength": 10_000,
                          "description": "The factual claim as ONE complete declarative "
                                         "sentence (not a question or a paragraph)."},
                "max_sources": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5,
                                "description": "How many sources to retrieve and weigh (1-10)."},
            },
            "required": ["claim"],
        },
    },
    {
        "name": "check_citations",
        "description": (
            "Fact-check every factual claim in a block of text, such as an AI-generated "
            "draft or document before it is published or acted on, and return a "
            "per-claim report. Claims are extracted automatically. Each reported claim "
            "carries a verdict, sufficiency (anything but sufficient is a reason to "
            "abstain or escalate) and a conformal guarantee when certified. The whole "
            "response is covered by a signed receipt bound to a hash of the submitted "
            "text, so it can later prove exactly which document was checked and what came "
            "back. Limits: only declarative factual sentences are checked (questions, "
            "opinions and instructions are skipped), at most max_claims of them, and "
            "evidence comes from retrievable web, encyclopedic and news sources, so very "
            "recent, private, niche-technical or opinion claims come back unverified or "
            "insufficient. Paid per call (x402): an unpaid call returns the payment offer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "maxLength": 100_000,
                         "description": "The prose whose factual claims should be extracted "
                                        "and checked."},
                "max_claims": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8,
                               "description": "Max number of extracted claims to verify (1-20)."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "resolve_instrument",
        "description": (
            "Resolve a security identifier (ticker, ISIN, CUSIP, SEDOL or FIGI) or an "
            "instrument name to canonical FIGI records via OpenFIGI, Bloomberg's open "
            "symbology, with provenance and a signed receipt. Returns matched, "
            "instruments and provenance. Useful whenever a claim, order or document names "
            "a security: it shows exactly which instrument is meant, disambiguating "
            "tickers that collide across exchanges, and the receipt proves the mapping. "
            "An explicit identifier resolves more reliably than a plain name; id_type is "
            "auto-detected from the value's shape when omitted. Limits: conservative by "
            "design, it resolves explicit identifiers and returns matched=false rather "
            "than guessing on an ambiguous plain name. It covers securities in OpenFIGI's "
            "symbology only and does not price instruments, return fundamentals or "
            "resolve crypto tokens. Paid per call (x402)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 200,
                          "description": "Ticker, ISIN, CUSIP, SEDOL, FIGI, or instrument name."},
                "id_type": {"type": "string",
                            "enum": ["TICKER", "ID_ISIN", "ID_CUSIP", "ID_SEDOL", "ID_BB_GLOBAL"],
                            "description": "Optional identifier type; auto-detected from the "
                                           "value's shape (e.g. 12-char alphanumeric -> ISIN)."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5,
                                "description": "Max canonical records to return (1-10)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "extract_claims",
        "description": (
            "Split text into independently checkable atomic factual claims: the low-cost "
            "first step of a verification loop (extract, ground, attest). Returns claims, "
            "count and input_sha256, plus a signed receipt bound to the input hash. "
            "Useful to see which claims a document makes before paying to ground them, to "
            "budget a verification pass by grounding only the claims that matter, or to "
            "prove later exactly which claims came from which text. Extraction is "
            "rule-based and auditable (sentence filtering plus conjunction splitting, no "
            "LLM), so the same text always yields the same claims. Limits: it keeps "
            "declarative factual sentences and skips questions, opinions, instructions "
            "and first-person statements; it splits only on high-precision conjunction "
            "boundaries, so a compound it cannot safely split stays whole. It verifies "
            "nothing: verdicts come from this server's grounding tools. Paid per call "
            "(x402), at the lowest price on this server."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "maxLength": 100_000,
                         "description": "The text to split into independently checkable "
                                        "atomic factual claims."},
                "max_claims": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20,
                               "description": "Max claims to return (1-50)."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "attest_delivery",
        "description": (
            "Neutral delivery verification for agentic commerce: after an x402 payment "
            "to another service, it checks what was delivered and returns a signed, "
            "offline-verifiable receipt binding payment, delivery and content "
            "(settlement receipt hash and decoded transaction fields, response sha256, "
            "structural conformance to the advertised schema, grounded verdicts on the "
            "response's factual claims). delivery_verdict, with a rationale: consistent "
            "(proceed), degraded (use with caution; refuted claims flagged), "
            "inconsistent (not reliable; the receipt is dispute evidence) or "
            "unverifiable (nothing contradicted or confirmed). Limits: it judges "
            "consistency with what was advertised, never service quality. Payment "
            "binding records which receipt was presented; on-chain confirmation is a "
            "separate step (the transaction hash is returned). Schema conformance is "
            "structural only; claim checks share the usual source-coverage limits. Paid "
            "per call (x402)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "maxLength": 500,
                            "description": "URL (or name) of the paid service whose "
                                           "delivery is being verified."},
                "response_text": {"type": "string", "maxLength": 100_000,
                                  "description": "The delivered payload, verbatim "
                                                 "(JSON or prose)."},
                "request_text": {"type": "string", "maxLength": 10_000,
                                 "description": "What was asked of the service "
                                                "(optional; bound by hash when given)."},
                "payment_receipt": {"type": "string", "maxLength": 16_384,
                                    "description": "x402 settlement receipt from the "
                                                   "paid call (X-PAYMENT-RESPONSE / "
                                                   "PAYMENT-RESPONSE value, base64 or "
                                                   "raw JSON)."},
                "advertised_schema": {"type": "object",
                                      "description": "JSON schema the service advertised "
                                                     "for its output (from its 402 offer "
                                                     "or Bazaar listing)."},
                "max_claims": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8,
                               "description": "Max claims in the delivered content to "
                                              "ground (1-20)."},
            },
            "required": ["service", "response_text"],
        },
    },
]

# Titles + behaviour annotations, applied in one sweep so a new tool cannot
# ship without them. Retrieval-backed tools reach the open web, which is what
# an absent openWorldHint already means (the spec default is true); scanners
# grade an explicit true as over-broad scope, so only the tool that is pure
# local text processing says openWorldHint=false. Nothing here mutates state.
_TOOL_TITLES = {
    "verify_claim": "Verify one claim (free)",
    "check_citations": "Fact-check a whole draft",
    "resolve_instrument": "Resolve a security to FIGI",
    "extract_claims": "Extract atomic claims",
    "attest_delivery": "Attest an x402 delivery",
}
_LOCAL_ONLY = {"extract_claims"}
for _t in TOOLS:
    _t["title"] = _TOOL_TITLES.get(_t["name"], _t["name"])
    _t["annotations"] = {
        "title": _t["title"],
        "readOnlyHint": True,
        "idempotentHint": True,
        "destructiveHint": False,
    }
    if _t["name"] in _LOCAL_ONLY:
        _t["annotations"]["openWorldHint"] = False
del _t

# Prompts: playbooks MCP clients surface as slash commands. They lead with
# the free tool; the paid bundle appears only where the receipt is the point.
PROMPTS = {
    "ground_before_answering": (
        "Ground a claim before asserting it",
        "Verify a factual claim with the free tool and branch on the "
        "machine-readable fields instead of vibes.",
        [{"name": "claim",
          "description": "The claim as one declarative sentence.",
          "required": True}],
        lambda a: (
            f"Verify this claim with verify_claim: {a.get('claim', '')!r}. "
            "Then branch exactly on the fields: assert it only if verdict "
            "is 'supported' AND sufficiency is 'sufficient' (cite the "
            "sources returned); abstain and say why on 'insufficient', "
            "'no_sources', 'no_stance' or 'conflict'; treat "
            "guarantee.certified true as a calibrated error bound you may "
            "gate decisions on. Report the receipt id so the check is "
            "provable later."
        ),
    ),
    "pre_publish_gate": (
        "Gate a draft before publishing",
        "Run every factual claim in a draft through verification and get a "
        "per-claim fix list with a signed receipt bound to the text.",
        [],
        lambda a: (
            "Take the draft I provide and gate it before publication: run "
            "check_citations on the full text (paid, x402; it returns a "
            "signed receipt bound to a hash of the exact text; if paying "
            "is not possible, fall back to verify_claim on each factual "
            "sentence, free). Produce a per-claim table: claim, verdict, "
            "sufficiency, and the fix (cite, soften, or delete). A claim "
            "that is not 'supported'+'sufficient' does not ship as fact. "
            "End with the receipt id and what it proves."
        ),
    ),
}


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


def payment_required_result(msg_id: Any, offer: dict, note: str) -> dict:
    """A payment requirement expressed as a TOOL RESULT rather than a transport error.

    MCP reports tool-execution failures in band, as a result carrying isError, and the
    streamable-HTTP transport treats any non-2xx as a transport-level fault. A bare
    HTTP 402 therefore makes the official SDK client raise StreamableHTTPError before
    a result object exists, so the calling agent never sees the offer, cannot pay, and
    cannot even report why. Every paid tool is unreachable over MCP that way even
    though the payment rail behind it is working — which is exactly what was happening
    here, across the whole MCP distribution surface (registry, Glama, PulseMCP,
    mcp.so, the URL connectors).

    So the offer is returned at HTTP 200 in a well-formed JSON-RPC envelope: machine
    readable in structuredContent, which is what x402's MCP client reads, and mirrored
    into content[0].text so a model driving the tool by hand can read the price and
    decide. The PAYMENT-REQUIRED header still rides along for x402-aware transports.
    """
    import json as _json

    payload = {"error": "payment_required", "note": note, "offer": offer}
    return _result(msg_id, {
        "content": [{"type": "text", "text": _json.dumps(payload, indent=2, default=str)}],
        "structuredContent": payload,
        "isError": True,
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
                    "note": "unpaid calls return an isError result carrying the "
                            "x402 offer in structuredContent; pay and retry with "
                            "X-PAYMENT or PAYMENT-SIGNATURE",
                }
            }
    return resp


def _handle_initialize(msg_id: Any, params: dict) -> dict:
    requested = params.get("protocolVersion")
    return _result(msg_id, {
        "protocolVersion": requested if isinstance(requested, str) else PROTOCOL_VERSION,
        "capabilities": {"tools": {}, "prompts": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION,
                       "title": "groundcheck: decision-grade fact verification",
                       "websiteUrl": "https://groundcheck.seiche.info"},
        # Describes the workflow by role and names no tool or other server:
        # scanners grade a tool name in the instructions as shadowing.
        "instructions": (
            "Groundcheck grounds factual claims before they are asserted or acted on. "
            "Its tools cover verifying a single claim (free), checking a whole draft, "
            "resolving which security a claim is about, splitting text into checkable "
            "atomic claims at low cost and, after a payment to another service over "
            "x402, verifying what that service delivered with a signed delivery "
            "receipt binding payment to delivery to grounded content: a neutral "
            "accountability trail for agentic commerce. Unlike a bare fact-checker, "
            "every answer is built to be acted on programmatically: a `sufficiency` "
            "tag says when to abstain or escalate, a conformal `guarantee` gives a "
            "distribution-free error bound to gate decisions on where the deployment "
            "is calibrated, compound claims are decomposed so a false part cannot "
            "hide, and a signed `provenance` receipt binds the exact evidence and "
            "model route so the path to each answer can be proven. Paid tools return "
            "an x402 payment offer when called without payment."
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
        return _result(msg_id, {"prompts": [
            {"name": n, "title": t, "description": d, "arguments": args}
            for n, (t, d, args, _fn) in PROMPTS.items()]})
    if method == "prompts/get":
        name = params.get("name")
        entry = PROMPTS.get(name) if isinstance(name, str) else None
        if entry is None:
            return _error(msg_id, INVALID_PARAMS, f"unknown prompt: {name}")
        _t, desc, args_spec, fn = entry
        p_args = params.get("arguments")
        if not isinstance(p_args, dict):
            p_args = {}
        missing = [a["name"] for a in args_spec
                   if a.get("required") and not p_args.get(a["name"])]
        if missing:
            return _error(msg_id, INVALID_PARAMS,
                          "missing required argument(s): " + ", ".join(missing))
        return _result(msg_id, {"description": desc, "messages": [
            {"role": "user", "content": {"type": "text", "text": fn(p_args)}}]})
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
