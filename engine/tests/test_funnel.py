"""Payment-funnel observability: does every paywall outcome leave a distinguishable trace?

The point of this module is to answer one operational question truthfully — did a real
buyer try to pay and fail, or was that just a census crawler? So the tests care about
(a) each stage being recorded with its reason, (b) known ecosystem infrastructure being
bucketed away from unidentified callers, and (c) observability never being able to break
a payment.
"""

import base64
import json
import os

os.environ["GROUNDCHECK_SEARCH_BACKEND"] = "stub"  # before app import

import pytest
from fastapi.testclient import TestClient

from groundcheck_engine import app as app_mod
from groundcheck_engine import funnel

TEXT = ("The Eiffel Tower is located in Paris and was completed in 1889. "
        "It was the tallest man-made structure in the world for 41 years.")
PAID = {"text": TEXT, "max_claims": 2}
OPS_TOKEN = "ops-secret-token"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GROUNDCHECK_X402_PAY_TO", raising=False)
    monkeypatch.delenv("GROUNDCHECK_FUNNEL_LOG", raising=False)
    monkeypatch.delenv("GROUNDCHECK_OPS_TOKEN", raising=False)
    # Hermetic: a test run inside a systemd unit would otherwise inherit a real
    # StateDirectory and start appending to production's durable log.
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    app_mod._free_used.clear()
    app_mod._hits.clear()
    funnel.reset()
    return TestClient(app_mod.app)


def _enable(monkeypatch, free_per_day=0):
    monkeypatch.setenv("GROUNDCHECK_X402_PAY_TO",
                       "0x000000000000000000000000000000000000dEaD")
    monkeypatch.setattr(app_mod.config, "X402_FREE_PER_DAY", free_per_day)


def _payment(version=2):
    return base64.b64encode(json.dumps(
        {"x402Version": version, "scheme": "exact", "network": "eip155:8453",
         "payload": {"signature": "0xsig", "authorization": {}}}
    ).encode()).decode()


def _facilitator(verify_ok=True, settle_ok=True):
    def fake_post(path, body):
        if path == "/verify":
            return ({"isValid": True} if verify_ok
                    else {"isValid": False, "invalidReason": "insufficient_funds"})
        if settle_ok:
            return {"success": True, "transaction": "0xtx", "network": "base",
                    "payer": "0xBuyer"}
        return {"success": False, "errorReason": "settle_reverted"}
    return fake_post


# ---- caller classification --------------------------------------------------

@pytest.mark.parametrize("ua,bucket", [
    ("CarbonMonitor/0.1 healthcheck (+https://carbon-cashmere.de)", "monitor"),
    ("x402-observer/1.0 (uptime+trust monitor; +https://x402.fuchss.app/trust)", "monitor"),
    ("CoinbaseBazaarDiscovery/1.0 (+https://docs.cdp.coinbase.com/x402)", "indexer"),
    ("AgentReeve/0.1 (independent x402 index; polite daily probe)", "indexer"),
    ("x402-census-probe/2.1 (independent index research)", "indexer"),
    ("ClaudeBot/1.0", "crawler"),
    # A pinned old axios is census traffic; x402-axios floats to current axios.
    ("axios/1.14.0", "indexer"),
    ("axios/1.18.1", "buyer-like"),
    ("x402-fetch/0.6.1", "buyer-like"),
    ("curl/8.7.1", "manual"),
    ("", "unknown"),
    ("SomeThingNobodyHasSeen/9", "unknown"),
])
def test_classify_agent(ua, bucket):
    assert funnel.classify_agent(ua) == bucket


def test_unknown_agents_are_never_absorbed_into_a_catch_all():
    """A new real buyer must surface as `unknown`, not be silently bucketed as a bot."""
    assert funnel.classify_agent("MysteryBuyer/2.0 (+https://example.invalid)") == "unknown"


REAL_PROBES = [
    "Mozilla/5.0 (compatible; Agent402/1.0; +https://github.com/MikeyPetrillo/Agent402)",
    "CarbonMonitor/0.1 healthcheck (+https://carbon-cashmere.de)",
    "x402-list-monitor/1.0 (+https://x402-list.com)",
    "x402-list-assessment/1.0 (+https://x402-list.com)",
    "preflight402-probe/0.1 (+https://github.com/chadander/preflight402)",
    "x402-observer/1.0 (uptime+trust monitor; +https://x402.fuchss.app/trust)",
    "402explorer/0.1 (+https://discover.paygent.net/about)",
    "forum-labs-trust-prober/1.0 (x402 endpoint QoS monitor; +https://forum-labs.com)",
    "attester.dev-watchtower/0.1 (sla probe)",
    "agent-tools.cloud-crawler/0.1 (+https://agent-tools.cloud)",
    "AgentReeve/0.1 (independent x402 index; polite daily probe)",
    "x402Scout/1.0 (https://app-production-cd86.up.railway.app)",
    "x402-census-probe/2.1 (independent index research)",
    "x402-reliability-probe/1.0",
    "entropy-daemon-trust-oracle/2.0",
    "Nitrograph-HealthCheck/1.0 (+https://api.nitrograph.com/bot)",
    "Mozilla/5.0 (compatible; railscope-verifier/0.2; +https://railscope)",
    "TrustBench-Prober/1.0",
    "CoinbaseBazaarDiscovery/1.0 (+https://docs.cdp.coinbase.com/x402)",
    "undertow-mm/claim-gate (self-check)",
]


@pytest.mark.parametrize("ua", REAL_PROBES)
def test_every_probe_seen_in_production_is_classified_as_infrastructure(ua):
    """Verbatim User-Agents from this service's own access logs. More than twenty
    distinct monitors, graders and indexes call the paid routes and not one of them
    is a buyer, so any of these landing in `unknown` would fabricate demand."""
    assert funnel.classify_agent(ua) in ("monitor", "indexer", "crawler",
                                         "scanner", "internal"), ua


def test_bare_runtime_user_agents_stay_buyer_like(client):
    """The legacy x402 client calls through global fetch, which identifies itself
    as bare `node` and nothing else — the exact shape a real v1 buyer arrives in."""
    for ua in ("node", "Deno/2.7.4", "undici", "bun/1.2"):
        assert funnel.classify_agent(ua) == "buyer-like", ua


def test_operator_shell_probes_do_not_pollute_the_demand_signal(client, monkeypatch):
    """Our own curl checks would otherwise read as unidentified buyers walking away."""
    _enable(monkeypatch)
    client.post("/check", json=PAID, headers={"User-Agent": "curl/8.7.1"})
    s = funnel.summary()
    assert s["stages"]["unpaid"] == 1
    assert s["unidentified_unpaid_posts"] == 0


# ---- one distinguishable trace per outcome ----------------------------------

def test_get_probe_is_recorded_as_probe_not_lost_demand(client, monkeypatch):
    _enable(monkeypatch)
    r = client.get("/check", headers={"User-Agent": "x402-census-probe/2.1"})
    assert r.status_code == 402
    s = funnel.summary()
    assert s["stages"]["probe"] == 1
    assert s["stages"]["unpaid"] == 0
    assert s["payment_attempts"] == 0


def test_unpaid_post_from_known_indexer_is_not_counted_as_a_lost_buyer(client, monkeypatch):
    _enable(monkeypatch)
    client.post("/check", json=PAID,
                headers={"User-Agent": "CoinbaseBazaarDiscovery/1.0"})
    s = funnel.summary()
    assert s["stages"]["unpaid"] == 1
    assert s["unidentified_unpaid_posts"] == 0     # the number that matters stays clean
    assert s["by_caller"]["indexer:unpaid"] == 1


def test_unpaid_post_from_unknown_caller_raises_the_signal(client, monkeypatch):
    _enable(monkeypatch)
    client.post("/check", json=PAID, headers={"User-Agent": "MysteryBuyer/2.0"})
    assert funnel.summary()["unidentified_unpaid_posts"] == 1


def test_malformed_payment_header_is_its_own_stage(client, monkeypatch):
    _enable(monkeypatch)
    r = client.post("/check", json=PAID, headers={"X-PAYMENT": "not-base64!!"})
    assert r.status_code == 402
    s = funnel.summary()
    assert s["stages"]["malformed"] == 1
    assert s["payment_attempts"] == 1             # someone TRIED to pay


def test_verify_failure_records_the_facilitator_reason(client, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(app_mod.x402, "_facilitator_post", _facilitator(verify_ok=False))
    r = client.post("/check", json=PAID, headers={"X-PAYMENT": _payment()})
    assert r.status_code == 402
    s = funnel.summary()
    assert s["stages"]["verify_fail"] == 1
    assert any("insufficient_funds" in k for k in s["drop_off_reasons"])
    assert s["recent"][-1]["dialect"] == "v2"
    assert s["recent"][-1]["amount_usd"] == 0.02


def test_settle_failure_is_distinguished_from_verify_failure(client, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(app_mod.x402, "_facilitator_post", _facilitator(settle_ok=False))
    r = client.post("/check", json=PAID, headers={"X-PAYMENT": _payment()})
    assert r.status_code == 402
    s = funnel.summary()
    assert s["stages"]["settle_fail"] == 1
    assert s["stages"]["verify_fail"] == 0
    assert any("settle_reverted" in k for k in s["drop_off_reasons"])


def test_settled_call_records_payer_and_transaction(client, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(app_mod.x402, "_facilitator_post", _facilitator())
    r = client.post("/check", json=PAID, headers={"X-PAYMENT": _payment()})
    assert r.status_code == 200
    s = funnel.summary()
    assert s["stages"]["paid"] == 1
    assert s["settled"] == 1
    assert s["conversion_of_attempts"] == 1.0
    assert s["recent"][-1]["tx"] == "0xtx"
    assert s["recent"][-1]["payer"] == "0xBuyer"


def test_free_quota_call_is_its_own_stage(client, monkeypatch):
    _enable(monkeypatch, free_per_day=1)
    r = client.post("/check", json=PAID)
    assert r.status_code == 200
    assert funnel.summary()["stages"]["free"] == 1


def test_v1_dialect_is_recorded_when_a_v1_buyer_pays(client, monkeypatch):
    """Which dialect buyers actually speak decides whether v1 compat is worth carrying."""
    _enable(monkeypatch)
    monkeypatch.setattr(app_mod.x402, "_facilitator_post", _facilitator())
    client.post("/check", json=PAID, headers={"X-PAYMENT": _payment(version=1)})
    assert funnel.summary()["recent"][-1]["dialect"] == "v1"


# ---- the durable copy -------------------------------------------------------

def test_events_append_to_the_jsonl_log_when_configured(client, monkeypatch, tmp_path):
    dest = tmp_path / "funnel.jsonl"
    monkeypatch.setenv("GROUNDCHECK_FUNNEL_LOG", str(dest))
    _enable(monkeypatch)
    client.post("/check", json=PAID, headers={"User-Agent": "MysteryBuyer/2.0"})
    lines = [json.loads(x) for x in dest.read_text().splitlines() if x.strip()]
    assert len(lines) == 1
    assert lines[0]["stage"] == "unpaid"
    assert lines[0]["caller"] == "unknown"
    assert lines[0]["path"] == "/check"


def test_an_unwritable_log_never_breaks_a_paid_call(client, monkeypatch):
    """Observability is strictly best-effort: a bad log path must not cost a sale."""
    monkeypatch.setenv("GROUNDCHECK_FUNNEL_LOG", "/nonexistent-dir/funnel.jsonl")
    _enable(monkeypatch)
    monkeypatch.setattr(app_mod.x402, "_facilitator_post", _facilitator())
    r = client.post("/check", json=PAID, headers={"X-PAYMENT": _payment()})
    assert r.status_code == 200
    assert "X-PAYMENT-RESPONSE" in r.headers


def test_an_unwritable_log_is_reported_loudly_to_the_operator(client, monkeypatch):
    """The production unit is hardened (ProtectHome=read-only), so an unwritable path
    is a live failure mode, and silence there looks identical to an idle service."""
    monkeypatch.setenv("GROUNDCHECK_FUNNEL_LOG", "/nonexistent-dir/funnel.jsonl")
    s = funnel.summary()
    assert s["log_ok"] is False
    assert "nonexistent-dir" in s["log_file"]


def test_state_directory_is_the_default_log_home_under_systemd(monkeypatch, tmp_path):
    """StateDirectory= is the one path a hardened unit is guaranteed to be able to
    write, so it is the default rather than something an operator has to discover."""
    monkeypatch.delenv("GROUNDCHECK_FUNNEL_LOG", raising=False)
    monkeypatch.setenv("STATE_DIRECTORY", f"{tmp_path}:/some/other/dir")
    ok, where = funnel.log_writable()
    assert ok is True
    assert where == str(tmp_path / "funnel.jsonl")


def test_explicit_log_path_wins_over_state_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("GROUNDCHECK_FUNNEL_LOG", str(tmp_path / "explicit.jsonl"))
    assert funnel.log_writable()[1] == str(tmp_path / "explicit.jsonl")


# ---- the operator surface ---------------------------------------------------

def test_ops_endpoint_is_invisible_without_a_token(client, monkeypatch):
    _enable(monkeypatch)
    assert client.get("/ops/funnel").status_code == 404


def test_ops_endpoint_404s_on_a_wrong_token_rather_than_401(client, monkeypatch):
    """A 401 would confirm the surface exists; revenue counts are not public."""
    _enable(monkeypatch)
    monkeypatch.setenv("GROUNDCHECK_OPS_TOKEN", OPS_TOKEN)
    assert client.get("/ops/funnel", params={"token": "wrong"}).status_code == 404
    assert client.get("/ops/funnel",
                      headers={"X-Ops-Token": "also-wrong-len"}).status_code == 404


def test_conformance_graders_never_get_a_free_200_even_when_a_quota_is_on(
        client, monkeypatch):
    """The free tier was switched off in production because a 200 on an unpaid POST
    reads as not-x402 to the graders and de-lists the service. Excluding recognised
    infrastructure makes the knob safe to turn on: graders keep seeing a clean 402
    while a caller that might actually buy can evaluate the output first."""
    _enable(monkeypatch, free_per_day=3)
    for ua in ("x402-list-assessment/1.0 (+https://x402-list.com)",
               "CoinbaseBazaarDiscovery/1.0 (+https://docs.cdp.coinbase.com/x402)",
               "Mozilla/5.0 (compatible; Agent402/1.0; +https://github.com/x/Agent402)"):
        r = client.post("/check", json=PAID, headers={"User-Agent": ua})
        assert r.status_code == 402, ua
    # ...while an unrecognised caller gets the trial
    r = client.post("/check", json=PAID, headers={"User-Agent": "MysteryBuyer/2.0"})
    assert r.status_code == 200
    assert r.headers["X-Groundcheck-Free-Remaining"] == "2"


def test_infrastructure_share_is_reported_so_the_ratio_is_visible(client, monkeypatch):
    _enable(monkeypatch)
    client.post("/check", json=PAID, headers={"User-Agent": "CarbonMonitor/0.1"})
    client.post("/check", json=PAID, headers={"User-Agent": "MysteryBuyer/2.0"})
    s = funnel.summary()
    assert s["ecosystem_infrastructure_hits"] == 1
    assert s["unidentified_unpaid_posts"] == 1


def test_ops_endpoint_serves_the_summary_with_the_token(client, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("GROUNDCHECK_OPS_TOKEN", OPS_TOKEN)
    client.post("/check", json=PAID, headers={"User-Agent": "MysteryBuyer/2.0"})
    for auth in ({"X-Ops-Token": OPS_TOKEN},
                 {"Authorization": f"Bearer {OPS_TOKEN}"}):
        r = client.get("/ops/funnel", headers=auth)
        assert r.status_code == 200
        assert r.json()["stages"]["unpaid"] == 1
        assert r.json()["unidentified_unpaid_posts"] == 1


def test_ops_summary_is_not_in_the_public_openapi(client, monkeypatch):
    _enable(monkeypatch)
    assert "/ops/funnel" not in client.get("/openapi.json").json()["paths"]


# ---- MCP transport shares the funnel ----------------------------------------

def test_paid_mcp_tool_call_is_recorded_under_the_mcp_transport(client, monkeypatch):
    _enable(monkeypatch)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "check_citations", "arguments": {"text": TEXT}}}
    r = client.post("/mcp", json=body, headers={"User-Agent": "MysteryBuyer/2.0"})
    # 200 with an in-band isError result: a non-2xx would make the MCP transport
    # throw before the agent ever sees the offer.
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is True
    s = funnel.summary()
    assert s["by_path"]["mcp:/check:unpaid"] == 1
