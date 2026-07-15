"""MCP over HTTP: the endpoint an agent adds by URL, no install.

Same contract as the REST surface — free verify_claim, paid check_citations and
resolve_instrument — spoken as JSON-RPC 2.0.
"""
import pytest
from fastapi.testclient import TestClient

from groundcheck_engine import app as app_module
from groundcheck_engine import instruments, mcp_http
from groundcheck_engine.app import app
from groundcheck_engine.models import Source

RPC = {"jsonrpc": "2.0", "id": 1}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GROUNDCHECK_X402_PAY_TO", raising=False)
    monkeypatch.setattr(app_module.config, "CACHE_TTL_S", 0)

    async def fake_pipeline(claim, max_sources):
        return ([Source(title="t", url="https://x", snippet="s", stance="supports")], "stub", None)

    monkeypatch.setattr(app_module, "_search_and_classify", fake_pipeline)

    async def no_figi(*a, **k):
        raise AssertionError("unexpected OpenFIGI call")

    monkeypatch.setattr(instruments, "_mapping", no_figi)
    monkeypatch.setattr(instruments, "_search", no_figi)
    monkeypatch.setattr(instruments.config, "RESOLVE_CACHE_TTL_S", 0)
    return TestClient(app)


def _enable_x402(monkeypatch):
    monkeypatch.setenv("GROUNDCHECK_X402_PAY_TO",
                       "0x000000000000000000000000000000000000dEaD")
    monkeypatch.setenv("GROUNDCHECK_X402_NETWORK", "base")


# ---- protocol ------------------------------------------------------------------

def test_initialize_declares_tools_capability(client):
    r = client.post("/mcp", json={**RPC, "method": "initialize",
                                  "params": {"protocolVersion": "2025-06-18"}})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["serverInfo"]["name"] == "groundcheck"
    assert "tools" in res["capabilities"]
    assert res["protocolVersion"] == "2025-06-18"


def test_tools_list_advertises_all_three(client):
    r = client.post("/mcp", json={**RPC, "method": "tools/list"})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == {"verify_claim", "check_citations", "resolve_instrument"}


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
    assert set(body["paid_tools"]) == {"check_citations", "resolve_instrument"}


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

def test_paid_tool_answers_402_when_quota_dry(client, monkeypatch):
    _enable_x402(monkeypatch)
    monkeypatch.setattr(app_module.config, "X402_FREE_PER_DAY", 0)
    r = client.post("/mcp", json={**RPC, "method": "tools/call",
                                  "params": {"name": "resolve_instrument",
                                             "arguments": {"query": "AAPL"}}})
    assert r.status_code == 402
    body = r.json()
    assert body["accepts"], "402 must carry an x402 offer"
    assert "paid tool" in body["error"]


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
