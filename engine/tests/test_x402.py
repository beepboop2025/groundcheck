"""x402 pay-per-call on /check: off by default, fail-closed everywhere else.

The facilitator is monkeypatched — no network. The retriever runs in stub
mode so /check itself never leaves the process. What's under test is the
gating logic: who gets 402, who gets served, that nothing is ever served on
an unverified or unsettled payment, and that both protocol generations
(v1 X-PAYMENT and v2 PAYMENT-SIGNATURE) are first-class.
"""

import base64
import json
import os

os.environ["GROUNDCHECK_SEARCH_BACKEND"] = "stub"  # before app import

import pytest
from fastapi.testclient import TestClient

from groundcheck_engine import app as app_mod
from groundcheck_engine import x402

TEXT = ("The Eiffel Tower is located in Paris and was completed in 1889. "
        "It was the tallest man-made structure in the world for 41 years.")
PAID = {"text": TEXT, "max_claims": 2}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GROUNDCHECK_X402_PAY_TO", raising=False)
    app_mod._free_used.clear()
    app_mod._hits.clear()
    return TestClient(app_mod.app)


def _enable(monkeypatch, free_per_day=None):
    monkeypatch.setenv("GROUNDCHECK_X402_PAY_TO",
                       "0x000000000000000000000000000000000000dEaD")
    if free_per_day is not None:
        monkeypatch.setattr(app_mod.config, "X402_FREE_PER_DAY", free_per_day)


def _payment(version=1, network="base"):
    return base64.b64encode(json.dumps(
        {"x402Version": version, "scheme": "exact", "network": network,
         "payload": {"signature": "0xsig", "authorization": {}}}
    ).encode()).decode()


def _good_facilitator(calls=None):
    def fake_post(path, body):
        if calls is not None:
            calls.append((path, body))
        if path == "/verify":
            return {"isValid": True}
        return {"success": True, "transaction": "0xtx", "network": "base"}
    return fake_post


# ---- off by default ---------------------------------------------------------

def test_disabled_means_old_behavior(client):
    r = client.post("/check", json=PAID)
    assert r.status_code == 200
    assert "X-Groundcheck-Free-Remaining" not in r.headers


def test_disabled_hides_manifest(client):
    assert client.get("/.well-known/x402").status_code == 404


# ---- free quota then payment-required ---------------------------------------

def test_free_quota_counts_down_then_402(client, monkeypatch):
    _enable(monkeypatch, free_per_day=2)
    r1 = client.post("/check", json=PAID)
    assert r1.status_code == 200
    assert r1.headers["X-Groundcheck-Free-Remaining"] == "1"
    r2 = client.post("/check", json=PAID)
    assert r2.headers["X-Groundcheck-Free-Remaining"] == "0"
    r3 = client.post("/check", json=PAID)
    assert r3.status_code == 402
    body = r3.json()
    assert body["x402Version"] == 2
    assert "quota" in body["error"]


def test_402_is_v2_native(client, monkeypatch):
    _enable(monkeypatch, free_per_day=0)
    r = client.post("/check", json=PAID)
    assert r.status_code == 402
    body = r.json()
    assert body["x402Version"] == 2
    assert body["resource"]["url"].endswith("/check")     # envelope-level object
    assert body["resource"]["mimeType"] == "application/json"
    nets = {a["network"] for a in body["accepts"]}
    assert nets == {"eip155:8453"}                        # CAIP-2 only, no v1 names
    for offer in body["accepts"]:
        assert offer["scheme"] == "exact"
        assert offer["payTo"].endswith("dEaD")
        assert offer["amount"] == "20000"                 # $0.02 in USDC atomic units
        assert "resource" not in offer and "description" not in offer
    # header envelope mirrors the body
    v2 = json.loads(base64.b64decode(r.headers["PAYMENT-REQUIRED"]))
    assert v2["x402Version"] == 2
    assert v2["accepts"][0]["network"] == "eip155:8453"


def test_verify_endpoint_stays_free(client, monkeypatch):
    _enable(monkeypatch, free_per_day=0)
    r = client.post("/verify", json={"claim": "The sky is sometimes blue at noon."})
    assert r.status_code == 200


# ---- paid path (facilitator mocked) ------------------------------------------

def test_valid_v1_payment_serves_and_returns_receipt(client, monkeypatch):
    _enable(monkeypatch, free_per_day=0)
    calls = []
    monkeypatch.setattr(x402, "_facilitator_post", _good_facilitator(calls))
    r = client.post("/check", json=PAID, headers={"X-PAYMENT": _payment()})
    assert r.status_code == 200
    assert [c[0] for c in calls] == ["/verify", "/settle"]
    receipt = json.loads(base64.b64decode(r.headers["X-PAYMENT-RESPONSE"]))
    assert receipt["transaction"] == "0xtx"
    assert r.headers["PAYMENT-RESPONSE"] == r.headers["X-PAYMENT-RESPONSE"]


def test_valid_v2_payment_via_payment_signature_header(client, monkeypatch):
    _enable(monkeypatch, free_per_day=0)
    calls = []
    monkeypatch.setattr(x402, "_facilitator_post", _good_facilitator(calls))
    r = client.post("/check", json=PAID,
                    headers={"PAYMENT-SIGNATURE": _payment(version=2,
                                                           network="eip155:8453")})
    assert r.status_code == 200
    # facilitator was addressed in the payer's dialect: v2 + CAIP-2 offer
    verify_body = calls[0][1]
    assert verify_body["x402Version"] == 2
    assert verify_body["paymentRequirements"]["network"] == "eip155:8453"


def test_invalid_payment_is_refused(client, monkeypatch):
    _enable(monkeypatch, free_per_day=0)
    monkeypatch.setattr(x402, "_facilitator_post",
                        lambda p, b: {"isValid": False, "invalidReason": "bad signature"})
    r = client.post("/check", json=PAID, headers={"X-PAYMENT": _payment()})
    assert r.status_code == 402
    assert "bad signature" in r.json()["error"]


def test_settle_failure_serves_nothing(client, monkeypatch):
    _enable(monkeypatch, free_per_day=0)

    def fake_post(path, body):
        if path == "/verify":
            return {"isValid": True}
        return {"success": False, "errorReason": "insufficient funds"}

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    r = client.post("/check", json=PAID, headers={"X-PAYMENT": _payment()})
    assert r.status_code == 402
    assert "insufficient funds" in r.json()["error"]
    assert "report" not in r.json()


def test_facilitator_outage_fails_closed(client, monkeypatch):
    _enable(monkeypatch, free_per_day=0)

    def fake_post(path, body):
        raise ConnectionError("facilitator down")

    monkeypatch.setattr(x402, "_facilitator_post", fake_post)
    r = client.post("/check", json=PAID, headers={"X-PAYMENT": _payment()})
    assert r.status_code == 402


def test_malformed_payment_header_is_402(client, monkeypatch):
    _enable(monkeypatch, free_per_day=0)
    r = client.post("/check", json=PAID, headers={"X-PAYMENT": "not-base64!!"})
    assert r.status_code == 402
    assert "malformed" in r.json()["error"]


def test_paid_call_bypasses_demo_rate_limit(client, monkeypatch):
    _enable(monkeypatch, free_per_day=0)
    monkeypatch.setattr(x402, "_facilitator_post", _good_facilitator())
    monkeypatch.setattr(app_mod, "_RL_MAX", 0)  # free surface fully throttled
    assert client.post("/verify", json={"claim": "x" * 30}).status_code == 429
    r = client.post("/check", json=PAID, headers={"X-PAYMENT": _payment()})
    assert r.status_code == 200


# ---- discovery ---------------------------------------------------------------

def test_manifest_lists_paid_resources(client, monkeypatch):
    _enable(monkeypatch)
    m = client.get("/.well-known/x402").json()
    assert m["x402Versions"] == [1, 2]
    assert m["payTo"].endswith("dEaD")
    by_path = {r["path"]: r for r in m["resources"]}
    assert set(by_path) == {"/check", "/resolve", "/extract", "/attest-delivery"}
    assert by_path["/check"]["priceUSD"] == 0.02
    assert by_path["/resolve"]["priceUSD"] == 0.005
    assert by_path["/extract"]["priceUSD"] == 0.005
    assert by_path["/attest-delivery"]["priceUSD"] == 0.05
    for res in by_path.values():
        assert {a["network"] for a in res["accepts"]} == {"eip155:8453"}
    assert "/verify" in m["alwaysFree"]


def test_manifest_json_alias_serves_same_document(client, monkeypatch):
    _enable(monkeypatch)
    assert (client.get("/.well-known/x402.json").json()
            == client.get("/.well-known/x402").json())


def test_health_reports_x402_state(client, monkeypatch):
    assert client.get("/health").json()["x402"] is False
    _enable(monkeypatch)
    assert client.get("/health").json()["x402"] is True


# --- discovery surface (x402scan, Bazaar indexers) --------------------------

def test_get_on_paid_path_answers_402_offer(client, monkeypatch):
    """Probes GET paid paths; the paywall must answer before method validation."""
    _enable(monkeypatch, free_per_day=5)
    r = client.get("/check")
    assert r.status_code == 402
    assert r.json()["accepts"], "402 must carry a valid accepts[] offer"
    # and it must not have burned the free quota
    assert client.post("/check", json=PAID).status_code == 200


def test_get_on_paid_path_still_405_when_x402_off(client):
    assert client.get("/check").status_code == 405


def test_favicon_served():
    c = TestClient(app_mod.app)
    r = c.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/x-icon"
    assert r.content[:4] == b"\x00\x00\x01\x00"


def test_openapi_marks_paid_and_free_surfaces():
    app_mod.app.openapi_schema = None  # force rebuild
    schema = app_mod.app.openapi()
    assert schema["info"]["contact"]["email"]
    assert "x402" in schema["components"]["securitySchemes"]
    paths = schema["paths"]
    assert paths["/check"]["post"]["security"] == [{"x402": []}]
    assert paths["/verify"]["post"]["security"] == []


def test_402_parses_with_official_sdk(client, monkeypatch):
    """The exact failure that blocked real buyers: the official SDK's
    PaymentRequired model must validate our 402 body verbatim."""
    from x402.schemas import PaymentRequired

    _enable(monkeypatch, free_per_day=0)
    r = client.post("/check", json=PAID)
    assert r.status_code == 402
    parsed = PaymentRequired.model_validate(r.json())
    assert parsed.x402_version == 2
    assert parsed.resource and parsed.resource.url.endswith("/check")
    assert parsed.accepts[0].amount == "20000"
    assert parsed.accepts[0].network == "eip155:8453"


def test_openapi_discovery_contract():
    app_mod.app.openapi_schema = None
    schema = app_mod.app.openapi()
    assert schema["info"]["x-guidance"]
    check_op = schema["paths"]["/check"]["post"]
    assert check_op["responses"]["402"]["description"] == "Payment Required"
    pinfo = check_op["x-payment-info"]
    assert pinfo["price"]["mode"] == "fixed"
    assert pinfo["price"]["currency"] == "USD"
    assert pinfo["protocols"] == [{"x402": {}}]
    assert check_op["requestBody"]["content"]["application/json"]["schema"]

