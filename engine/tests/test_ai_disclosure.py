"""AI disclosure: where a language model sits, said the same way everywhere.

One step of Groundcheck uses a language model: stance classification on
retrieved evidence. /verify and /check are therefore an opt-in AI service, the
claim checks inside /attest-delivery use the same step, and /extract and
/resolve use no model. The hosted price list keeps /check off for that reason.
That only holds if every surface says so and the code agrees, so this file
pins the statement in four places:

1. the code: the grounding routes reach the model step, the rule-based routes
   never do, and the New York Fed route answers without it;
2. every result (REST JSON and MCP tool text) carries the statement for its
   route from disclosure.py, and the signed receipts still verify;
3. the advertised MCP metadata on both surfaces carries the short forms, and
   no description, instruction or prompt lets a verdict make the decision (AI
   never approves; the scanner rules themselves live in
   test_metadata_hygiene.py);
4. the copy: README, npm README, both llms.txt copies, landing page,
   docs/x402.md, docs/delivery-attestation.md and SKILL.md disclose it, /check
   carries no typed price, and every other typed price equals the engine's
   configured default.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Before any engine import, as in the other app-level test files: config reads
# the retrieval backend once, at import, and this file sorts first. Without the
# stub here every later file would retrieve from the live web.
os.environ["GROUNDCHECK_SEARCH_BACKEND"] = "stub"

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from groundcheck_engine import app as app_module  # noqa: E402
from groundcheck_engine import attest, config, disclosure, funnel, instruments  # noqa: E402
from groundcheck_engine import mcp_http, official_rates, retrieval  # noqa: E402
from groundcheck_engine.landing import LANDING_HTML  # noqa: E402
from groundcheck_engine.models import Source  # noqa: E402
from test_metadata_hygiene import DASHES, URL, _ts_tools  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CLAIM = "The Eiffel Tower is located in Paris, France."
SOFR_CLAIM = "SOFR was 3.88% on 2026-09-29."
TEXT = ("The Eiffel Tower is located in Paris and was completed in 1889. "
        "It was the tallest man-made structure in the world for 41 years.")
BANNED = ("partner", "rbi approved", "credit rating", "predicts", "early warning")
STATEMENTS = {
    "GROUNDING": disclosure.GROUNDING, "DELIVERY": disclosure.DELIVERY,
    "EXTRACT": disclosure.EXTRACT, "RESOLVE": disclosure.RESOLVE,
    "TOOL_GROUNDING": disclosure.TOOL_GROUNDING,
    "TOOL_DELIVERY": disclosure.TOOL_DELIVERY,
    "TOOL_RULE_BASED": disclosure.TOOL_RULE_BASED,
    "TOOL_FINANCE": disclosure.TOOL_FINANCE, "SERVER_NOTE": disclosure.SERVER_NOTE,
}


# ---- fixtures ------------------------------------------------------------------

@pytest.fixture()
def model_calls():
    return []


@pytest.fixture()
def client(monkeypatch, model_calls):
    """x402 off, hermetic counters, live retrieval replaced by one passage, and
    both model entry points replaced by stubs that record every call. Any live
    HTTP call left over fails at once rather than waiting on the network."""
    async def no_network(self, request, *args, **kwargs):
        raise AssertionError(f"unexpected network call: {request.url}")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.delenv("GROUNDCHECK_X402_PAY_TO", raising=False)
    monkeypatch.delenv("GROUNDCHECK_FUNNEL_LOG", raising=False)
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    app_module._hits.clear()
    app_module._free_used.clear()
    funnel.reset()
    monkeypatch.setattr(app_module.config, "CACHE_TTL_S", 0)
    monkeypatch.setattr(app_module.config, "ENSEMBLE", True)

    async def one_passage(query, max_sources=5):
        return [Source(title="Eiffel Tower", url="https://example.org/eiffel",
                       snippet="The Eiffel Tower is in Paris.")]

    async def panel(claim, sources, weights=None):
        model_calls.append(("panel", claim))
        for s in sources:
            s.stance = "supports"
        return "free-llm-router:stub", 0.9, {"stub": 0.9}

    async def single(claim, sources):
        model_calls.append(("single", claim))
        return "free-llm-router"

    monkeypatch.setattr(app_module.retriever, "search", one_passage)
    monkeypatch.setattr(app_module.ensemble, "classify_panel", panel)
    monkeypatch.setattr(app_module, "classify_stances", single)

    async def one_figi(id_type, value):
        return [{"figi": "BBG000B9XRY4", "name": "APPLE INC", "ticker": "AAPL",
                 "exchCode": "US", "securityType": "Common Stock",
                 "marketSector": "Equity"}]

    async def no_search(*a, **k):
        raise AssertionError("unexpected OpenFIGI search")

    monkeypatch.setattr(instruments, "_mapping", one_figi)
    monkeypatch.setattr(instruments, "_search", no_search)
    monkeypatch.setattr(instruments.config, "RESOLVE_CACHE_TTL_S", 0)
    return TestClient(app_module.app)


def _post(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    return r.json()


def _mcp_payload(client, name, **arguments):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": name, "arguments": arguments}}
    result = client.post("/mcp", json=msg).json()["result"]
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ---- 1. the statements themselves ----------------------------------------------

@pytest.mark.parametrize("name", sorted(STATEMENTS))
def test_statement_is_house_style(name):
    text = STATEMENTS[name]
    assert not DASHES.search(text), f"{name} carries an em or en dash"
    assert not URL.search(text), f"{name} carries a URL"
    assert not [w for w in BANNED if w in text.lower()], name


def test_statements_say_what_each_route_is():
    g = disclosure.GROUNDING
    assert g.startswith("Opt-in AI service:") and "language model" in g
    assert "Wikipedia" in g and "GDELT" in g and "finance" in g
    assert "SOFR" in g and "New York Fed" in g and "no model" in g
    assert "rule-based" in disclosure.DELIVERY and "language model" in disclosure.DELIVERY
    assert disclosure.EXTRACT.startswith("No language model")
    assert disclosure.RESOLVE.startswith("No language model")


# ---- 2. the code agrees with the statements ------------------------------------

def test_grounding_routes_reach_the_model_step(client, model_calls):
    _post(client, "/verify", {"claim": CLAIM})
    assert model_calls, "/verify ran without the language model step"
    model_calls.clear()
    _post(client, "/check", {"text": TEXT})
    assert model_calls, "/check ran without the language model step"
    model_calls.clear()
    # The claim checks inside the delivery receipt reuse the /check pipeline.
    _post(client, "/attest-delivery", {"service": "https://svc.example/x",
                                       "response_text": TEXT})
    assert model_calls, "/attest-delivery grounded claims without the model step"


def test_rule_based_routes_never_reach_the_model(client, model_calls):
    body = _post(client, "/extract", {"text": TEXT})
    assert body["count"] >= 1
    body = _post(client, "/resolve", {"query": "AAPL"})
    assert body["matched"] is True
    assert model_calls == []


def test_official_rate_claims_skip_the_model(client, model_calls, monkeypatch):
    monkeypatch.setattr(app_module.retriever, "backend", "wikipedia+gdelt")

    async def published(reference):
        return [Source(title="NY Fed SOFR", url="https://example.org/sofr",
                       snippet="SOFR 3.88 on 2026-09-29", stance="supports")]

    monkeypatch.setattr(official_rates, "compare", published)
    body = _post(client, "/verify", {"claim": SOFR_CLAIM})
    assert body["classifier"] == "official-nyfed-numeric"
    assert body["ai_disclosure"] == disclosure.GROUNDING
    assert model_calls == []


def test_retrieval_defaults_to_wikipedia_and_gdelt(monkeypatch):
    monkeypatch.setattr(retrieval.config, "SEARCH_BACKEND", None)
    monkeypatch.setattr(retrieval.config, "SEARCH_URL", None)
    monkeypatch.setattr(retrieval.config, "NEWS_BACKEND", True)
    assert retrieval.Retriever().backend == "wikipedia+gdelt"


def test_calibration_set_holds_no_finance_figures():
    groups = json.loads((ROOT / "engine/calibration/calibration.json").read_text())["groups"]
    assert set(groups) <= {"global", "general", "instrument"}, \
        "a new calibration group: update disclosure.py and the copy"
    seeds = [json.loads(line)["claim"]
             for line in (ROOT / "engine/calibration/seed_claims.jsonl").read_text().splitlines()
             if line.strip()]
    assert seeds and not [c for c in seeds if "%" in c], \
        "finance figures in the calibration set: update disclosure.py and the copy"


# ---- 3. every result carries its statement -------------------------------------

def test_rest_results_carry_their_statement(client):
    verify = _post(client, "/verify", {"claim": CLAIM})
    assert verify["ai_disclosure"] == disclosure.GROUNDING
    assert _post(client, "/check", {"text": TEXT})["ai_disclosure"] == disclosure.GROUNDING
    assert _post(client, "/extract", {"text": TEXT})["ai_disclosure"] == disclosure.EXTRACT
    assert _post(client, "/resolve", {"query": "AAPL"})["ai_disclosure"] == disclosure.RESOLVE
    delivery = _post(client, "/attest-delivery", {"service": "https://svc.example/x",
                                                  "response_text": TEXT})
    assert delivery["ai_disclosure"] == disclosure.DELIVERY
    # The field sits outside every signed manifest, so receipts still verify.
    assert attest.verify_attested_response("verify", verify)["valid"] is True
    assert attest.verify_attested_response("delivery", delivery)["valid"] is True
    assert "ai_disclosure" not in verify["attestation"]["manifest_keys"]


def test_mcp_tool_text_carries_the_statement(client):
    assert _mcp_payload(client, "verify_claim", claim=CLAIM)["ai_disclosure"] \
        == disclosure.GROUNDING
    assert _mcp_payload(client, "check_citations", text=TEXT)["ai_disclosure"] \
        == disclosure.GROUNDING
    assert _mcp_payload(client, "extract_claims", text=TEXT)["ai_disclosure"] \
        == disclosure.EXTRACT


def test_check_still_answers_with_an_offer(client, monkeypatch):
    """The copy says an unpaid /check still gets an x402 offer. If /check
    pricing is ever switched off on the server, this fails: update the copy."""
    monkeypatch.setenv("GROUNDCHECK_X402_PAY_TO", "0x000000000000000000000000000000000000dEaD")
    monkeypatch.setenv("GROUNDCHECK_X402_NETWORK", "base")
    monkeypatch.setattr(app_module.config, "X402_FREE_PER_DAY", 0)
    r = client.post("/check", json={"text": TEXT})
    assert r.status_code == 402 and r.json()["accepts"]


# ---- 4. advertised metadata, both surfaces --------------------------------------

def _hosted():
    return {t["name"]: t["description"] for t in mcp_http.TOOLS}


def _stdio():
    return {t["name"]: t["description"] for t in _ts_tools()}


@pytest.mark.parametrize("surface", [_hosted, _stdio], ids=["hosted", "stdio"])
def test_tool_descriptions_disclose_the_model(surface):
    d = surface()
    for name in ("verify_claim", "check_citations"):
        assert disclosure.TOOL_GROUNDING in d[name], name
        assert disclosure.FINANCE_PHRASE in d[name], name
        assert "deterministic" not in d[name].lower(), name
    assert disclosure.TOOL_DELIVERY in d["attest_delivery"]
    assert disclosure.TOOL_RULE_BASED in d["resolve_instrument"]
    assert "rule-based" in d["extract_claims"] and "no LLM" in d["extract_claims"]


def test_server_instructions_disclose_the_model():
    init = mcp_http._handle_initialize(1, {})["result"]
    assert disclosure.SERVER_NOTE in init["instructions"]


# AI never approves: a model-assisted verdict is a signal for a person or a
# rule, so nothing an agent reads may offer it as the gate on a decision.
GATES_A_DECISION = re.compile(r"gate (?:a |the )?decisions?", re.I)


def _agent_facing_text():
    init = mcp_http._handle_initialize(1, {})["result"]
    yield "hosted instructions", init["instructions"]
    for surface, tools in (("hosted", _hosted()), ("stdio", _stdio())):
        for name, text in tools.items():
            yield f"{surface} {name}", text
    for name, (title, desc, args, body) in mcp_http.PROMPTS.items():
        yield f"prompt {name}", " ".join((title, desc, body({a["name"]: "x" for a in args})))
    for rel in ("README.md", "server/README.md", "llms.txt", "SKILL.md",
                "docs/x402.md", "docs/delivery-attestation.md"):
        yield rel, _read(rel)


def test_no_surface_lets_a_verdict_make_the_decision():
    bad = [where for where, text in _agent_facing_text() if GATES_A_DECISION.search(text)]
    assert not bad, f"a verdict offered as the gate on a decision: {bad}"
    for surface in (_hosted, _stdio):
        assert "a person or a rule decides" in surface()["verify_claim"], surface.__name__


# ---- 5. the copy ----------------------------------------------------------------

def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _section(text: str, heading: str = "## AI disclosure") -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


def _landing_note() -> str:
    m = re.search(r'<p class="note" id="ai-disclosure">(.*?)</p>', LANDING_HTML, re.S)
    assert m, "the landing page lost its AI disclosure"
    return m.group(1)


COPY = {
    "README.md": lambda: _section(_read("README.md")),
    "server/README.md": lambda: _section(_read("server/README.md")),
    "llms.txt": lambda: _section(_read("llms.txt")),
    "SKILL.md": lambda: _section(_read("SKILL.md")),
    "docs/delivery-attestation.md": lambda: _section(_read("docs/delivery-attestation.md")),
    "landing page": _landing_note,
}


@pytest.mark.parametrize("where", sorted(COPY))
def test_copy_discloses_the_model(where):
    text = COPY[where]()
    assert "opt-in AI service" in text, where
    assert "language model" in text and "finance" in text, where
    assert "rule-based" in text, where
    assert not DASHES.search(text), f"{where}: em or en dash in the disclosure"
    assert not [w for w in BANNED if w in text.lower()], where


def test_both_llms_txt_copies_match():
    assert _read("llms.txt") == _read("engine/groundcheck_engine/llms.txt")


def test_readme_table_covers_every_route():
    table = _section(_read("README.md"))
    for route in ("/verify", "/check", "/attest-delivery", "/extract", "/resolve"):
        assert f"`POST {route}`" in table, route


def test_delivery_doc_names_the_model_step_in_the_bundle():
    doc = _read("docs/delivery-attestation.md")
    note = _section(doc)
    assert "claim checks" in note and "language model" in note
    assert "no claim verdict here is a finance guarantee" in note
    assert "language model" in _section(doc, "## What it does not prove")


def _amount(value: float) -> str:
    return f"{value:g}"


def test_check_carries_no_typed_price():
    check = _amount(config.X402_PRICES_USD["/check"])
    readme = _section(_read("README.md"), "### Machine-payable hosting (x402)")
    pricing = _section(_read("llms.txt"), "## Pricing")
    docs = _read("docs/x402.md").split("## Enabling it")[0]
    delivery = _read("docs/delivery-attestation.md")
    for where, text in (("README", readme), ("llms.txt", pricing), ("docs/x402.md", docs),
                        ("docs/delivery-attestation.md", delivery)):
        assert f"${check}" not in text and f"USD {check}" not in text \
            and f"({check})" not in text, f"{where} still lists the /check price"
        assert "not on the price list" in text, where
    remote = _section(_read("llms.txt"), "## Remote MCP (no install)")
    assert "check_citations is not on the price list" in remote
    assert not DASHES.search(remote), "llms.txt Remote MCP line: em or en dash"


def test_typed_prices_match_the_engine_defaults():
    prices = config.X402_PRICES_USD
    readme = _section(_read("README.md"), "### Machine-payable hosting (x402)")
    found = re.findall(r"`(/[a-z-]+)`\s+\$(\d+(?:\.\d+)?)", readme)
    assert {p for p, _ in found} == {"/extract", "/resolve", "/attest-delivery"}
    for path, amount in found:
        assert float(amount) == prices[path], ("README", path)

    for line in _section(_read("llms.txt"), "## Pricing").splitlines():
        m = re.match(r"- POST (/[a-z-]+)", line)
        if m and m.group(1) in prices:
            for amount in re.findall(r"USD (\d+(?:\.\d+)?)", line):
                assert float(amount) == prices[m.group(1)], ("llms.txt", line)
    loop = re.search(r"extract cheap \((\d+\.\d+)\).*bundle \((\d+\.\d+)\)", _read("llms.txt"))
    assert loop and float(loop.group(1)) == prices["/extract"] \
        and float(loop.group(2)) == prices["/attest-delivery"]

    docs = _read("docs/x402.md")
    rows = re.findall(r"\| `POST (/[a-z-]+)`[^|]*\| \*\*\$(\d+(?:\.\d+)?)/call\*\* \|", docs)
    assert {p for p, _ in rows} == {"/extract", "/resolve", "/attest-delivery"}
    for path, amount in rows:
        assert float(amount) == prices[path], ("docs/x402.md", path)
    loop = re.search(r"extract cheap \(\$(\d+\.\d+)\).*?bundle \(\$(\d+\.\d+)", docs, re.S)
    assert loop and float(loop.group(1)) == prices["/extract"] \
        and float(loop.group(2)) == prices["/attest-delivery"]

    delivery = _read("docs/delivery-attestation.md")
    rows = re.findall(r"\| `POST (/[a-z-]+)` \| \$(\d+(?:\.\d+)?) \|", delivery)
    assert {p for p, _ in rows} == {"/extract", "/attest-delivery"}
    for path, amount in rows:
        assert float(amount) == prices[path], ("docs/delivery-attestation.md", path)
    assert len(re.findall(r"\$\d", delivery)) == len(rows), \
        "docs/delivery-attestation.md types a price outside its price table"
