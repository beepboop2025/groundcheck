"""MCP over HTTP: the endpoint an agent adds by URL, no install.

Same contract as the REST surface — free verify_claim, paid check_citations and
resolve_instrument — spoken as JSON-RPC 2.0.
"""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from groundcheck_engine import app as app_module
from groundcheck_engine import funnel, instruments, mcp_http, x402
from groundcheck_engine.app import app
from groundcheck_engine.models import Source

RPC = {"jsonrpc": "2.0", "id": 1}
TEXT = ("The Eiffel Tower is located in Paris and was completed in 1889. "
        "It was the tallest man-made structure in the world for 41 years.")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GROUNDCHECK_X402_PAY_TO", raising=False)
    # Hermetic: the rate limiter, the free-tier ledger and the funnel counters
    # are process-global, so a test that inherits them measures the file's
    # execution order rather than the endpoint.
    monkeypatch.delenv("GROUNDCHECK_FUNNEL_LOG", raising=False)
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    app_module._hits.clear()
    app_module._free_used.clear()
    funnel.reset()
    monkeypatch.setattr(app_module.config, "CACHE_TTL_S", 0)

    async def fake_pipeline(claim, max_sources):
        return ([Source(title="t", url="https://x", snippet="s", stance="supports")], "stub", None)

    monkeypatch.setattr(app_module, "_search_and_classify", fake_pipeline)

    async def no_figi(*a, **k):
        raise AssertionError("unexpected OpenFIGI call")

    monkeypatch.setattr(instruments, "_mapping", no_figi)
    monkeypatch.setattr(instruments, "_search", no_figi)
    monkeypatch.setattr(instruments.config, "RESOLVE_CACHE_TTL_S", 0)
    # An unhandled exception must surface as the 500 an anonymous caller would
    # actually receive, not as a raise inside the test.
    return TestClient(app, raise_server_exceptions=False)


def _enable_x402(monkeypatch, free_per_day=0):
    monkeypatch.setenv("GROUNDCHECK_X402_PAY_TO",
                       "0x000000000000000000000000000000000000dEaD")
    monkeypatch.setenv("GROUNDCHECK_X402_NETWORK", "base")
    monkeypatch.setattr(app_module.config, "X402_FREE_PER_DAY", free_per_day)


def _payment():
    return base64.b64encode(json.dumps(
        {"x402Version": 2, "scheme": "exact", "network": "eip155:8453",
         "payload": {"signature": "0xsig", "authorization": {}}}
    ).encode()).decode()


def _call(msg_id, name, **arguments):
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def _by_id(response):
    return {m["id"]: m["result"] for m in response.json()}


def _is_offer(result):
    return (result["isError"] is True
            and result["structuredContent"]["error"] == "payment_required"
            and bool(result["structuredContent"]["offer"]["accepts"]))


# ---- protocol ------------------------------------------------------------------

def test_initialize_declares_tools_capability(client):
    r = client.post("/mcp", json={**RPC, "method": "initialize",
                                  "params": {"protocolVersion": "2025-06-18"}})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["serverInfo"]["name"] == "groundcheck"
    assert "tools" in res["capabilities"]
    assert res["protocolVersion"] == "2025-06-18"


def test_tools_list_advertises_all_five(client):
    r = client.post("/mcp", json={**RPC, "method": "tools/list"})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == {"verify_claim", "check_citations", "resolve_instrument",
                     "extract_claims", "attest_delivery"}


def test_ping_and_empty_capabilities(client):
    assert client.post("/mcp", json={**RPC, "method": "ping"}).json()["result"] == {}
    assert client.post("/mcp", json={**RPC, "method": "resources/list"}).json()["result"] == {"resources": []}


def test_notification_gets_202_no_body(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202
    assert r.content == b""


def test_unknown_method_and_bad_message(client):
    r = client.post("/mcp", json={**RPC, "method": "does/not/exist"})
    assert r.json()["error"]["code"] == mcp_http.METHOD_NOT_FOUND
    r = client.post("/mcp", json={"id": 1, "method": "ping"})  # no jsonrpc field
    assert r.json()["error"]["code"] == mcp_http.INVALID_REQUEST


def test_batch_is_capped(client):
    msgs = [{**RPC, "id": i, "method": "ping"} for i in range(mcp_http.MAX_BATCH + 1)]
    r = client.post("/mcp", json=msgs)
    assert r.status_code == 413


def test_get_describes_the_endpoint(client):
    body = client.get("/mcp").json()
    assert body["transport"] == "streamable-http"
    assert set(body["paid_tools"]) == {"check_citations", "resolve_instrument",
                                       "extract_claims", "attest_delivery"}


# ---- tools ---------------------------------------------------------------------

def test_verify_claim_tool_runs_free(client):
    r = client.post("/mcp", json={**RPC, "method": "tools/call",
                                  "params": {"name": "verify_claim",
                                             "arguments": {"claim": "The sky is blue."}}})
    assert r.status_code == 200
    payload = r.json()["result"]
    assert payload["isError"] is False
    assert "supported" in payload["content"][0]["text"]


def test_resolve_instrument_tool_calls_the_engine(client, monkeypatch):
    async def fake_mapping(id_type, value):
        return [{"figi": "BBG000B9XRY4", "name": "APPLE INC", "ticker": "AAPL"}]
    monkeypatch.setattr(instruments, "_mapping", fake_mapping)

    r = client.post("/mcp", json={**RPC, "method": "tools/call",
                                  "params": {"name": "resolve_instrument",
                                             "arguments": {"query": "AAPL"}}})
    text = r.json()["result"]["content"][0]["text"]
    assert "BBG000B9XRY4" in text and "OpenFIGI" in text


def test_unknown_tool_is_invalid_params(client):
    r = client.post("/mcp", json={**RPC, "method": "tools/call",
                                  "params": {"name": "nope", "arguments": {}}})
    assert r.json()["error"]["code"] == mcp_http.INVALID_PARAMS


def test_tool_error_is_reported_not_raised(client):
    r = client.post("/mcp", json={**RPC, "method": "tools/call",
                                  "params": {"name": "verify_claim", "arguments": {}}})
    assert r.status_code == 200
    assert r.json()["error"]["code"] == mcp_http.INVALID_PARAMS  # missing 'claim'


# ---- payment -------------------------------------------------------------------

def test_paid_tool_offers_payment_as_a_jsonrpc_result_not_a_transport_error(
        client, monkeypatch):
    """REGRESSION: a bare HTTP 402 made every paid tool unreachable over MCP.

    The streamable-HTTP transport raises on any non-2xx, so the official SDK client
    threw StreamableHTTPError before a result object existed: the agent never saw the
    offer, could not pay, and could not report why. Payment is a tool-execution
    condition, so it belongs in band, at HTTP 200, as a result carrying isError.
    """
    _enable_x402(monkeypatch)
    monkeypatch.setattr(app_module.config, "X402_FREE_PER_DAY", 0)
    r = client.post("/mcp", json={**RPC, "method": "tools/call",
                                  "params": {"name": "resolve_instrument",
                                             "arguments": {"query": "AAPL"}}})
    assert r.status_code == 200, "non-2xx makes the SDK transport throw"
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1, "the request id must be echoed or the client cannot match"
    result = body["result"]
    assert result["isError"] is True
    offer = result["structuredContent"]["offer"]          # what x402's MCP client reads
    assert offer["accepts"], "the offer must still carry x402 payment requirements"
    assert "paid tool" in result["structuredContent"]["note"]
    # mirrored for a model driving the tool by hand
    assert "payment_required" in result["content"][0]["text"]
    # x402-aware transports can still read the header envelope
    assert r.headers["PAYMENT-REQUIRED"]


def test_no_mcp_path_ever_answers_non_2xx_for_a_priced_tool(client, monkeypatch):
    """The defect class, pinned: any non-2xx on /mcp is invisible to an MCP client."""
    _enable_x402(monkeypatch)
    monkeypatch.setattr(app_module.config, "X402_FREE_PER_DAY", 0)
    for name, args in (("resolve_instrument", {"query": "AAPL"}),
                       ("check_citations", {"text": "The sky is blue."}),
                       ("extract_claims", {"text": "The sky is blue."}),
                       ("attest_delivery", {"service": "https://x.invalid",
                                            "response_text": "{}"})):
        r = client.post("/mcp", json={**RPC, "method": "tools/call",
                                      "params": {"name": name, "arguments": args}})
        assert r.status_code == 200, f"{name} answered {r.status_code}"
        assert r.json()["result"]["isError"] is True


def test_malformed_payment_on_mcp_also_stays_in_band(client, monkeypatch):
    _enable_x402(monkeypatch)
    monkeypatch.setattr(app_module.config, "X402_FREE_PER_DAY", 0)
    r = client.post("/mcp", json={**RPC, "method": "tools/call",
                                  "params": {"name": "resolve_instrument",
                                             "arguments": {"query": "AAPL"}}},
                    headers={"X-PAYMENT": "not-base64!!"})
    assert r.status_code == 200
    assert "malformed" in r.json()["result"]["structuredContent"]["note"]


def test_free_tool_never_pays(client, monkeypatch):
    _enable_x402(monkeypatch)
    monkeypatch.setattr(app_module.config, "X402_FREE_PER_DAY", 0)
    r = client.post("/mcp", json={**RPC, "method": "tools/call",
                                  "params": {"name": "verify_claim",
                                             "arguments": {"claim": "The sky is blue."}}})
    assert r.status_code == 200


def test_tools_list_annotates_prices_for_wallets(client, monkeypatch):
    _enable_x402(monkeypatch)
    r = client.post("/mcp", json={**RPC, "method": "tools/list"})
    by_name = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert "_meta" not in by_name["verify_claim"]
    meta = by_name["resolve_instrument"]["_meta"]["x402"]
    assert meta["price"]["amount"] == "0.005000"
    assert meta["payTo"].endswith("dEaD")


# ---- batch metering ------------------------------------------------------------

def test_a_two_message_batch_cannot_smuggle_a_paid_tool_past_the_paywall(
        client, monkeypatch):
    """REGRESSION: the paywall only ever priced a body of exactly one message.

    Two or more messages left the price lookup at None, skipped the payment block
    whole, and fell through to the dispatch loop, which ran check_citations,
    attest_delivery, resolve_instrument and extract_claims for free, signed
    attestation receipts included. No payment, no quota, and no funnel event, so
    the leak was invisible in the operator's own numbers.
    """
    _enable_x402(monkeypatch)
    r = client.post("/mcp", json=[{**RPC, "id": 1, "method": "ping"},
                                  _call(2, "check_citations", text=TEXT)])
    assert r.status_code == 200
    results = _by_id(r)
    assert results[1] == {}, "the free message in the batch still runs"
    assert _is_offer(results[2])
    assert set(results[2]["structuredContent"]) == {"error", "note", "offer"}, \
        "the offer replaces the report and its signed receipt, it does not accompany it"
    assert r.headers["PAYMENT-REQUIRED"]


def test_a_full_batch_of_paid_tools_buys_nothing(client, monkeypatch):
    _enable_x402(monkeypatch)
    batch = [_call(i, "extract_claims", text=TEXT) for i in range(mcp_http.MAX_BATCH)]
    r = client.post("/mcp", json=batch)
    assert r.status_code == 200
    results = _by_id(r)
    assert len(results) == mcp_http.MAX_BATCH
    assert all(_is_offer(v) for v in results.values())


def test_free_quota_is_charged_once_per_priced_message_in_a_batch(client, monkeypatch):
    """Metering is per message, not per request: three paid calls cost three units."""
    _enable_x402(monkeypatch, free_per_day=3)
    batch = [_call(i, "extract_claims", text=TEXT) for i in range(mcp_http.MAX_BATCH)]
    results = _by_id(client.post("/mcp", json=batch))
    served = [v for v in results.values() if v["isError"] is False]
    assert len(served) == 3
    assert len([v for v in results.values() if _is_offer(v)]) == mcp_http.MAX_BATCH - 3
    # and the ledger is spent, so a second batch from the same caller pays
    again = _by_id(client.post("/mcp", json=batch))
    assert all(_is_offer(v) for v in again.values())


def test_a_batch_of_only_free_tools_still_works(client, monkeypatch):
    _enable_x402(monkeypatch)
    r = client.post("/mcp", json=[{**RPC, "id": 1, "method": "tools/list"},
                                  _call(2, "verify_claim", claim="The sky is blue.")])
    assert r.status_code == 200
    results = _by_id(r)
    by_name = {t["name"]: t for t in results[1]["tools"]}
    assert set(by_name) == {"verify_claim", "check_citations", "resolve_instrument",
                            "extract_claims", "attest_delivery"}
    meta = by_name["check_citations"]["_meta"]["x402"]
    assert meta["price"]["amount"] == f"{x402.price_usd('/check'):.6f}"
    assert meta["payTo"].endswith("dEaD")
    assert "_meta" not in by_name["verify_claim"], "the free tool carries no price"
    assert results[2]["isError"] is False
    assert "supported" in results[2]["content"][0]["text"]
    assert "PAYMENT-REQUIRED" not in r.headers


def test_a_single_priced_message_is_unchanged(client, monkeypatch):
    _enable_x402(monkeypatch)
    r = client.post("/mcp", json=_call(1, "resolve_instrument", query="AAPL"))
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict), "a lone message answers with an object, not a list"
    assert body["id"] == 1
    assert _is_offer(body["result"])
    assert "paid tool" in body["result"]["structuredContent"]["note"]
    assert r.headers["PAYMENT-REQUIRED"]


def test_a_paid_single_message_still_settles_and_returns_the_result(client, monkeypatch):
    _enable_x402(monkeypatch)
    monkeypatch.setattr(x402, "_facilitator_post", lambda path, body: (
        {"isValid": True} if path == "/verify"
        else {"success": True, "transaction": "0xtx", "payer": "0xBuyer"}))
    r = client.post("/mcp", json=_call(1, "extract_claims", text=TEXT),
                    headers={"X-PAYMENT": _payment()})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict) and body["id"] == 1
    assert body["result"]["isError"] is False
    assert "Eiffel" in body["result"]["content"][0]["text"]
    assert r.headers["X-PAYMENT-RESPONSE"]


def test_one_payment_cannot_cover_two_paid_tools_in_one_body(client, monkeypatch):
    """A payment authorizes one price, so a body asking two is refused whole.

    Anything else dispatches more work than the authorization pays for, which is
    the same leak with a payment header stapled to it.
    """
    _enable_x402(monkeypatch)
    asked = []

    def fake_post(path, body):
        asked.append(path)
        return ({"isValid": True} if path == "/verify"
                else {"success": True, "transaction": "0xtx", "payer": "0xBuyer"})

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    r = client.post("/mcp", json=[_call(1, "extract_claims", text=TEXT),
                                  _call(2, "check_citations", text=TEXT)],
                    headers={"X-PAYMENT": _payment()})
    results = _by_id(r)
    assert all(_is_offer(v) for v in results.values())
    assert "one paid tool per request" in results[1]["structuredContent"]["note"]
    assert asked == [], "nothing was verified or settled"


def test_a_malformed_payment_blocks_every_priced_message_in_a_batch(client, monkeypatch):
    _enable_x402(monkeypatch, free_per_day=5)
    r = client.post("/mcp", json=[_call(1, "extract_claims", text=TEXT),
                                  {**RPC, "id": 2, "method": "ping"}],
                    headers={"X-PAYMENT": "not-base64!!"})
    results = _by_id(r)
    assert _is_offer(results[1]), "a bad header never falls back to the free quota"
    assert "malformed" in results[1]["structuredContent"]["note"]
    assert results[2] == {}


# ---- funnel visibility ---------------------------------------------------------

def test_every_mcp_post_leaves_a_funnel_line(client, monkeypatch):
    """The durable half of the batch fix: a gap that records nothing is a gap
    nobody finds. Batches used to reach dispatch without a single event."""
    _enable_x402(monkeypatch)
    client.post("/mcp", json=[{**RPC, "id": 1, "method": "ping"},
                              _call(2, "check_citations", text=TEXT),
                              _call(3, "extract_claims", text=TEXT)])
    s = funnel.summary()
    assert s["stages"]["unpaid"] == 2, "one line per priced message, not per body"
    assert s["by_path"]["mcp:/check:unpaid"] == 1
    assert s["by_path"]["mcp:/extract:unpaid"] == 1

    funnel.reset()
    client.post("/mcp", json=[{**RPC, "id": 1, "method": "ping"},
                              {**RPC, "id": 2, "method": "tools/list"}])
    s = funnel.summary()
    assert sum(s["stages"].values()) == 1, "a body that owes nothing is recorded too"
    assert s["by_path"]["mcp:unpriced:probe"] == 1


def test_a_metered_batch_records_free_and_paid_stages(client, monkeypatch):
    _enable_x402(monkeypatch, free_per_day=1)
    client.post("/mcp", json=[_call(1, "extract_claims", text=TEXT),
                              _call(2, "extract_claims", text=TEXT)])
    s = funnel.summary()
    assert s["stages"]["free"] == 1
    assert s["stages"]["unpaid"] == 1


def test_a_failed_settlement_withdraws_the_paid_result_without_rerunning_the_batch(
        client, monkeypatch):
    _enable_x402(monkeypatch)
    calls = []
    real_dispatch = mcp_http.dispatch

    async def counting_dispatch(msg, handlers):
        calls.append(mcp_http.tool_name(msg) or (msg or {}).get("method"))
        return await real_dispatch(msg, handlers)

    monkeypatch.setattr(mcp_http, "dispatch", counting_dispatch)
    monkeypatch.setattr(x402, "_facilitator_post", lambda path, body: (
        {"isValid": True} if path == "/verify"
        else {"success": False, "errorReason": "settle_reverted"}))
    r = client.post("/mcp", json=[_call(1, "extract_claims", text=TEXT),
                                  {**RPC, "id": 2, "method": "ping"}],
                    headers={"X-PAYMENT": _payment()})
    results = _by_id(r)
    assert _is_offer(results[1]), "no settlement, no result"
    assert "settle_reverted" in results[1]["structuredContent"]["note"]
    assert results[2] == {}
    assert calls == ["extract_claims", "ping"], "the batch runs once, not twice"
    assert funnel.summary()["stages"]["settle_fail"] == 1


# ---- malformed message shapes --------------------------------------------------

MALFORMED_PARAMS = [
    pytest.param(["check_citations"], id="params-is-a-list"),
    pytest.param("check_citations", id="params-is-a-string"),
    pytest.param(None, id="params-is-null"),
    pytest.param(7, id="params-is-a-number"),
    pytest.param({"name": None}, id="name-is-null"),
]


@pytest.mark.parametrize("params", MALFORMED_PARAMS)
def test_a_malformed_tools_call_answers_json_rpc_instead_of_500(
        client, monkeypatch, params):
    """The pricing pass reads params.name on EVERY message in the body now, so a
    params that is not an object must not reach a .get() as an anonymous 500."""
    _enable_x402(monkeypatch)
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                  "method": "tools/call", "params": params})
    assert r.status_code == 200, r.text
    assert r.json()["error"]["code"] == mcp_http.INVALID_PARAMS


@pytest.mark.parametrize("params", MALFORMED_PARAMS)
def test_a_malformed_message_does_not_take_the_rest_of_the_batch_down(
        client, monkeypatch, params):
    _enable_x402(monkeypatch)
    r = client.post("/mcp", json=[{**RPC, "id": 1, "method": "ping"},
                                  {"jsonrpc": "2.0", "id": 2,
                                   "method": "tools/call", "params": params},
                                  _call(3, "check_citations", text=TEXT)])
    assert r.status_code == 200, r.text
    by_id = {m["id"]: m for m in r.json()}
    assert by_id[1]["result"] == {}, "the legitimate free message still runs"
    assert by_id[2]["error"]["code"] == mcp_http.INVALID_PARAMS
    assert _is_offer(by_id[3]["result"]), "the paid message is still refused"
    # the crash used to land before the first seen() call, so the body that
    # triggered it was the one body the operator could not see
    assert funnel.summary()["by_path"]["mcp:/check:unpaid"] == 1


def test_a_malformed_message_is_never_priced_as_a_paid_tool(client, monkeypatch):
    """No shape of malformed params may name a paid tool the paywall then charges
    for, because dispatch will not run one either."""
    _enable_x402(monkeypatch, free_per_day=5)
    for params in (["check_citations"], "check_citations", None,
                   {"name": ["check_citations"], "arguments": {}}):
        client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                  "method": "tools/call", "params": params})
    s = funnel.summary()
    assert s["stages"]["free"] == 0 and s["stages"]["unpaid"] == 0
    r = client.post("/mcp", json=_call(1, "check_citations", text=TEXT))
    assert r.json()["result"]["isError"] is False, "the daily allowance is intact"


# ---- funnel keys are bounded ---------------------------------------------------

def test_a_flood_of_distinct_methods_cannot_grow_the_funnel_key_space(
        client, monkeypatch):
    """funnel keys its reason counter with a Counter that never evicts, so caller
    text may not reach it. 40 distinct long method names, one bucket."""
    _enable_x402(monkeypatch)
    for i in range(40):
        client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                  "method": f"attack{i:04d}" * 12})
    reasons = funnel.summary()["drop_off_reasons"]
    assert list(reasons) == ["probe:other"]
    # the rate limiter turns some of the 40 away, which is the ceiling the
    # exposure note projects 43k keys per IP per day from
    assert reasons["probe:other"] > 1


def test_a_flood_of_distinct_tool_names_cannot_grow_the_funnel_key_space(
        client, monkeypatch):
    _enable_x402(monkeypatch)
    for i in range(40):
        client.post("/mcp", json=_call(1, f"attack{i:04d}" * 12, text=TEXT))
    assert list(funnel.summary()["drop_off_reasons"]) == ["probe:other"]


def test_every_funnel_reason_a_body_can_produce_is_a_known_token(
        client, monkeypatch):
    """The whole vocabulary, exhaustively: mixed bodies of real and junk shapes
    can only ever key on names this service publishes."""
    _enable_x402(monkeypatch)
    vocabulary = ({"other", "malformed"}
                  | set(app_module._MCP_TOOL_NAMES)
                  | set(app_module._MCP_METHODS))
    junk = ["evil" * 30, {"jsonrpc": "2.0", "id": 1, "method": "x" * 200},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "y" * 200}}]
    for i in range(20):
        body = [{**RPC, "id": 1, "method": "ping"},
                {**RPC, "id": 2, "method": "tools/list"},
                _call(3, "verify_claim", claim="The sky is blue."),
                junk[i % len(junk)],
                {"jsonrpc": "2.0", "id": 5, "method": f"probe{i}"}]
        client.post("/mcp", json=body)
    for key in funnel.summary()["drop_off_reasons"]:
        stage, _, reason = key.partition(":")
        assert stage == "probe"
        tokens = [t for t in reason.split(",") if t != "..."]
        assert len(tokens) <= app_module._MCP_SHAPE_TOKENS
        assert set(tokens) <= vocabulary, key


# ---- notifications are not metered ---------------------------------------------

def test_a_paid_tool_sent_as_a_notification_spends_no_free_quota(client, monkeypatch):
    """dispatch answers a notification with nothing and never reaches the handler,
    so the caller receives no work and must not be charged for any. Behind shared
    NAT, charging would let one caller drain the allowance of every other."""
    _enable_x402(monkeypatch, free_per_day=2)
    note = {"jsonrpc": "2.0", "method": "tools/call",
            "params": {"name": "extract_claims", "arguments": {"text": TEXT}}}
    for _ in range(6):
        assert client.post("/mcp", json=note).status_code == 202
    s = funnel.summary()
    assert s["stages"]["free"] == 0 and s["stages"]["unpaid"] == 0

    served = [client.post("/mcp", json=_call(i, "extract_claims", text=TEXT)).json()
              for i in range(2)]
    assert all(r["result"]["isError"] is False for r in served), \
        "the notifications spent nothing, so both allowance units remain"
    assert client.post("/mcp", json=_call(3, "extract_claims", text=TEXT)) \
        .json()["result"]["structuredContent"]["error"] == "payment_required"


def test_a_message_that_is_not_json_rpc_2_spends_no_free_quota(client, monkeypatch):
    """dispatch refuses it before the handler, so it buys nothing."""
    _enable_x402(monkeypatch, free_per_day=1)
    r = client.post("/mcp", json={"jsonrpc": "1.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "extract_claims",
                                             "arguments": {"text": TEXT}}})
    assert r.json()["error"]["code"] == mcp_http.INVALID_REQUEST
    assert funnel.summary()["stages"]["free"] == 0
    assert client.post("/mcp", json=_call(2, "extract_claims", text=TEXT)) \
        .json()["result"]["isError"] is False


def test_a_notification_still_cannot_reach_a_paid_tool(client, monkeypatch):
    """Not metering notifications must not become a way to run one for free."""
    _enable_x402(monkeypatch)
    ran = []
    real = app_module._MCP_HANDLERS["extract_claims"]

    async def watched(**kw):
        ran.append(kw)
        return await real(**kw)

    monkeypatch.setitem(app_module._MCP_HANDLERS, "extract_claims", watched)
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/call",
                                  "params": {"name": "extract_claims",
                                             "arguments": {"text": TEXT}}})
    assert r.status_code == 202 and r.content == b""
    assert ran == [], "the handler never ran, which is why it was not charged"
