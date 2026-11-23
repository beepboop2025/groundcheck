"""Directory-scanner hygiene gate for the advertised MCP surfaces.

Automated MCP directories (Andrax Pentester's MCPGrade, mcpgrade.dev,
VerifyMCP and others) grade a server from tools/list, the prompt list and the
server instructions alone. They never read the code. A description that names
a sibling tool is graded as tool shadowing, an explicit openWorldHint=true as
over-broad scope, a URL in a tool description as an outbound pointer, and a
description over 1,000 characters as oversized, whatever the code does. This
test holds both surfaces to the fleet rules before anything ships: the hosted
streamable-HTTP endpoint (mcp_http.py) and the npm stdio server
(server/src/server.ts, which container hosts such as Glama introspect).

Rules (fleet canon of 23 Nov 2026; keep in step with the fleet audit script):
 1. A tool's metadata describes only that tool: no other tool's name appears
    in its title, description or parameter text.
 2. The server instructions and prompt descriptions name no tool; they
    describe the workflow by role.
 3. No credential or personal-data trigger words in advertised metadata.
 4. No imperatives aimed at the model ("before answering", "ignore
    previous", "IMPORTANT:", "use these tools first" and the like).
 5. No invisible, zero-width or bidi control characters.
 6. No URL in tool metadata, and no URL in the instructions in a sentence
    with a fetch, send, call or see verb (graded as fetch-and-obey).
 7. openWorldHint is never set to true explicitly: the spec default is
    already true, and scanners grade the explicit flag as over-broad scope.
 8. Every description stays under 1,000 characters.
 9. No parameter asks for a secret.
10. Every free-text string parameter is bounded (enum, const, pattern,
    format or maxLength).
11. No em or en dashes, the house rule for anything that ships.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from groundcheck_engine import mcp_http

SENSITIVE = re.compile(
    r"pass(?:word|words|wd)|credential|credit[ -]?card|card number|\bcvv\b|\bssn\b"
    r"|social security|\bpii\b|personal data|\bsecrets?\b|api[ _-]?keys?\b|private key"
    r"|seed phrase|webhook|callback url|\bfeedback\b",
    re.I,
)
IMPERATIVE = re.compile(
    r"ignore (?:all |any )?(?:previous|prior)|disregard (?:all|any|previous)"
    r"|before (?:you )?answer(?:ing)?|instead of answering|you must|always (?:call|use)"
    r"|do not tell|\bIMPORTANT:|reach for (?:these|this) tools? first"
    r"|use (?:these|this) tools? first|\bFIRST,? before\b",
    re.I,
)
INVISIBLE = re.compile(
    "[​-‏‪-‮⁠-⁤⁦-⁩﻿︀-️"
    "\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
)
URL = re.compile(r"https?://[^\s)\"'>\]]+", re.I)
FETCH_VERB = re.compile(
    r"\b(fetch|read|visit|open|download|go to|load|retrieve|follow|consult|refer to"
    r"|see|check|call|send|sends|handoff|hand off)\b",
    re.I,
)
DASHES = re.compile("[–—]")
SECRET_PARAM = re.compile(
    r"^(pass(word)?|passwd|secret|api[_-]?key|apikey|token|auth|bearer|private[_-]?key)$", re.I
)
BOUNDED = ("enum", "const", "pattern", "format", "maxLength")
TS_SERVER = Path(__file__).resolve().parents[2] / "server" / "src" / "server.ts"


# ---- the hosted surface, read the way a scanner reads it -----------------------

def _rpc(method: str) -> dict:
    msg = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}
    return asyncio.run(mcp_http.dispatch(msg, {}))["result"]


@pytest.fixture(scope="module")
def hosted():
    """initialize, tools/list and prompts/list as served, x402 metadata included."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GROUNDCHECK_X402_PAY_TO", "0x000000000000000000000000000000000000dEaD")
        init = _rpc("initialize")
        tools = mcp_http.annotate_tools_list({"result": _rpc("tools/list")})["result"]["tools"]
        prompts = _rpc("prompts/list")["prompts"]
    return init, tools, prompts


def _tool_text(tool: dict) -> str:
    parts = [tool.get("title") or "", tool.get("description") or "",
             (tool.get("annotations") or {}).get("title") or "",
             json.dumps(tool.get("_meta") or {}, ensure_ascii=False)]
    for name, spec in (tool.get("inputSchema") or {}).get("properties", {}).items():
        parts.append(f"{name}: {json.dumps(spec, ensure_ascii=False)}")
    return "\n".join(parts)


def _prompt_text(prompt: dict) -> str:
    parts = [prompt.get("title") or "", prompt.get("description") or ""]
    for arg in prompt.get("arguments") or []:
        parts.append(f"{arg['name']}: {arg.get('description') or ''}")
    return "\n".join(parts)


# ---- the npm stdio surface, read statically from its source --------------------
# The stdio server needs Node, its dependencies and an engine to answer
# tools/list, none of which this suite has, so its registrations are parsed
# from server.ts. The parse fails loudly rather than skipping a tool it cannot
# read.

_LITERAL = r'"(?:[^"\\\n]|\\.)*"'


def _unquote(literal: str) -> str:
    return re.sub(r"\\(.)", r"\1", literal[1:-1])


def _ts_tools() -> list[dict]:
    src = TS_SERVER.read_text(encoding="utf-8")
    blocks = src.split("server.tool(")[1:]
    tools = []
    for block in blocks:
        head = block.split("async (", 1)[0]
        m = re.match(rf"\s*({_LITERAL})\s*,\s*((?:{_LITERAL}\s*\+?\s*)+),", head)
        assert m, f"cannot parse a server.tool registration: {head[:80]!r}"
        params = {}
        for p in re.finditer(r"(?m)^ {4}(\w+): z\b(.*?)(?=,\n {4}\w+: z\b|,\n {2}\})",
                             head[m.end():], re.S):
            chain = p.group(2)
            params[p.group(1)] = {
                "text": " ".join(_unquote(x) for x in re.findall(_LITERAL, chain)),
                "string": bool(re.match(r"\s*\.string\(", chain)),
                "bounded": bool(re.search(r"\.(?:max|length|regex|email|url|uuid)\(", chain)),
            }
        tools.append({
            "name": _unquote(m.group(1)),
            "description": "".join(_unquote(x) for x in re.findall(_LITERAL, m.group(2))),
            "params": params,
        })
    assert len(tools) == src.count("server.tool(")
    return tools


@pytest.fixture(scope="module")
def stdio():
    return _ts_tools()


def _units(hosted, stdio):
    """Every block of advertised text with the tool names it must not mention."""
    init, tools, prompts = hosted
    hosted_names = [t["name"] for t in tools]
    stdio_names = [t["name"] for t in stdio]
    yield "hosted instructions", init.get("instructions") or "", hosted_names
    yield "hosted server info", json.dumps(init["serverInfo"], ensure_ascii=False), hosted_names
    for t in tools:
        yield f"hosted tool {t['name']}", _tool_text(t), [n for n in hosted_names if n != t["name"]]
    for p in prompts:
        yield f"hosted prompt {p['name']}", _prompt_text(p), hosted_names
    for t in stdio:
        text = "\n".join([t["description"]] + [f"{k}: {v['text']}" for k, v in t["params"].items()])
        yield f"stdio tool {t['name']}", text, [n for n in stdio_names if n != t["name"]]


def _names_in(text: str, names) -> list[str]:
    return [n for n in names
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", text)]


def test_both_surfaces_were_read(hosted, stdio):
    _init, tools, _prompts = hosted
    hosted_names = {t["name"] for t in tools}
    stdio_names = {t["name"] for t in stdio}
    assert len(hosted_names) == 5 and hosted_names <= stdio_names
    assert all(t["description"] and t["params"] is not None for t in stdio)


def test_metadata_never_names_another_tool(hosted, stdio):
    bad = []
    for where, text, forbidden in _units(hosted, stdio):
        hits = _names_in(text, forbidden)
        if hits:
            bad.append(f"{where} names {hits}")
    assert not bad, "describe the workflow by role, not by tool name:\n" + "\n".join(bad)


@pytest.mark.parametrize("rule,pattern", [
    ("credential or personal-data trigger word", SENSITIVE),
    ("imperative aimed at the model", IMPERATIVE),
    ("invisible or bidi control character", INVISIBLE),
    ("em or en dash", DASHES),
])
def test_metadata_has_no_scanner_trigger(hosted, stdio, rule, pattern):
    bad = []
    for where, text, _forbidden in _units(hosted, stdio):
        m = pattern.search(text)
        if m:
            a = max(0, m.start() - 40)
            bad.append(f"{where}: ...{text[a:m.end() + 40]!r}")
    assert not bad, f"{rule} in advertised metadata:\n" + "\n".join(bad)


def test_tool_metadata_carries_no_url(hosted, stdio):
    _init, tools, _prompts = hosted
    bad = [f"hosted {t['name']}" for t in tools
           if URL.search(t.get("description") or "")
           or URL.search(json.dumps((t.get("inputSchema") or {}).get("properties", {})))]
    bad += [f"stdio {t['name']}" for t in stdio
            if URL.search(t["description"]) or any(URL.search(v["text"]) for v in t["params"].values())]
    assert not bad, f"URL in tool metadata: {bad}"


def test_instructions_never_pair_a_url_with_a_fetch_verb(hosted):
    init, _tools, _prompts = hosted
    text = init.get("instructions") or ""
    bad = []
    for m in URL.finditer(text):
        start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
        if FETCH_VERB.search(text[start:m.end() + 80]):
            bad.append(m.group(0))
    assert not bad, f"URL beside a fetch verb in the instructions: {bad}"


def test_open_world_hint_is_never_explicitly_true(hosted):
    _init, tools, _prompts = hosted
    bad = [t["name"] for t in tools if (t.get("annotations") or {}).get("openWorldHint") is True]
    assert not bad, f"explicit openWorldHint=true (the spec default already is): {bad}"


def test_descriptions_stay_short(hosted, stdio):
    _init, tools, _prompts = hosted
    bad = [f"hosted {t['name']}: {len(t['description'])}" for t in tools
           if len(t.get("description") or "") >= 1000]
    bad += [f"stdio {t['name']}: {len(t['description'])}" for t in stdio
            if len(t["description"]) >= 1000]
    assert not bad, f"descriptions of 1,000 characters or more: {bad}"


def test_no_parameter_asks_for_a_secret(hosted, stdio):
    _init, tools, _prompts = hosted
    bad = [f"hosted {t['name']}.{p}" for t in tools
           for p in (t.get("inputSchema") or {}).get("properties", {}) if SECRET_PARAM.match(p)]
    bad += [f"stdio {t['name']}.{p}" for t in stdio for p in t["params"] if SECRET_PARAM.match(p)]
    assert not bad, f"secret-shaped parameters: {bad}"


def test_every_free_text_parameter_is_bounded(hosted, stdio):
    _init, tools, _prompts = hosted
    bad = []
    for t in tools:
        for p, spec in (t.get("inputSchema") or {}).get("properties", {}).items():
            for variant in spec.get("anyOf") or [spec]:
                if variant.get("type") == "string" and not any(k in variant for k in BOUNDED):
                    bad.append(f"hosted {t['name']}.{p}")
    bad += [f"stdio {t['name']}.{p}" for t in stdio for p, v in t["params"].items()
            if v["string"] and not v["bounded"]]
    assert not bad, f"unbounded free-text parameters: {bad}"


def test_hosted_bounds_match_what_the_engine_accepts(hosted):
    """A bound never rejects what the REST models already accept for the same field."""
    from groundcheck_engine.app import AttestDeliveryRequest, ExtractRequest, ResolveRequest

    def model_max(model, field):
        return next(m.max_length for m in model.model_fields[field].metadata
                    if getattr(m, "max_length", None) is not None)

    _init, tools, _prompts = hosted
    props = {t["name"]: t["inputSchema"]["properties"] for t in tools}
    assert props["resolve_instrument"]["query"]["maxLength"] == model_max(ResolveRequest, "query")
    assert props["extract_claims"]["text"]["maxLength"] == model_max(ExtractRequest, "text")
    for field in ("service", "response_text", "request_text", "payment_receipt"):
        assert props["attest_delivery"][field]["maxLength"] == model_max(AttestDeliveryRequest, field)


def test_the_gate_catches_what_the_scanner_caught():
    """The rules fire on the exact wording the fleet audit flagged on 0.7.2."""
    names = ["verify_claim", "check_citations", "extract_claims", "attest_delivery"]
    assert _names_in("per-claim report, the batch form of verify_claim", names) == ["verify_claim"]
    assert _names_in("Ground claims before asserting them (verify_claim), verify a whole "
                     "draft (check_citations)", names) == ["verify_claim", "check_citations"]
    assert URL.search('attest_delivery({"service": "https://api.vendor.xyz/enrich"})')
    assert IMPERATIVE.search("Reach for these tools FIRST, before answering from memory")
    assert DASHES.search("groundcheck — decision-grade fact verification")
    assert not _names_in("verifying a single claim and checking a whole draft", names)
