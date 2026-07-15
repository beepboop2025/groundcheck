"""Delivery verification (/attest-delivery) and claim extraction (/extract).

No network: the retriever runs in stub mode and the pure functions in
delivery.py are tested directly. What's under test: the schema-conformance
walker, settlement-receipt decoding, the delivery-verdict rule, both new
endpoints end-to-end (including offline receipt verification and tamper
detection), the MCP tools, and the x402 pricing of the new paths.
"""
import base64
import json
import os

os.environ["GROUNDCHECK_SEARCH_BACKEND"] = "stub"  # before app import

import pytest
from fastapi.testclient import TestClient

from groundcheck_engine import app as app_mod
from groundcheck_engine import attest, delivery

SEED = "22" * 32  # deterministic test key (never use a fixed seed in prod)

DELIVERED_JSON = json.dumps({"figi": "BBG000B9XRY4", "name": "APPLE INC",
                             "note": "The Eiffel Tower was completed in 1889."})
DELIVERED_PROSE = ("The Eiffel Tower was completed in 1889. "
                   "It was the tallest man-made structure for 41 years.")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GROUNDCHECK_X402_PAY_TO", raising=False)
    monkeypatch.setenv(attest.ENV_KEY, SEED)
    app_mod._free_used.clear()
    app_mod._hits.clear()
    app_mod._verdict_cache.clear()
    return TestClient(app_mod.app)


# ---- schema conformance (structural subset) -----------------------------------

def test_conformance_valid_object():
    schema = {"type": "object", "required": ["figi", "name"],
              "properties": {"figi": {"type": "string"},
                             "name": {"type": "string"}}}
    assert delivery.conformance_problems(json.loads(DELIVERED_JSON), schema) == []


def test_conformance_missing_required_and_wrong_type():
    schema = {"type": "object", "required": ["figi", "price"],
              "properties": {"name": {"type": "integer"}}}
    problems = delivery.conformance_problems(json.loads(DELIVERED_JSON), schema)
    assert any("price" in p for p in problems)
    assert any("$.name" in p and "integer" in p for p in problems)


def test_conformance_type_miss_stops_deeper_checks():
    problems = delivery.conformance_problems(
        "just a string", {"type": "object", "required": ["x"]})
    assert len(problems) == 1 and "advertised type" in problems[0]


def test_conformance_items_and_enum():
    schema = {"type": "array",
              "items": {"type": "object", "required": ["v"],
                        "properties": {"v": {"enum": ["a", "b"]}}}}
    assert delivery.conformance_problems([{"v": "a"}, {"v": "b"}], schema) == []
    problems = delivery.conformance_problems([{"v": "z"}, {}], schema)
    assert any("[0].v" in p and "enum" in p for p in problems)
    assert any("[1]" in p and "'v'" in p for p in problems)


def test_conformance_tolerates_unknown_keywords_and_bad_schema():
    assert delivery.conformance_problems({"a": 1}, {"type": "object",
                                                    "weirdKeyword": 42}) == []
    assert delivery.conformance_problems({"a": 1}, "not a schema") == []
    # integer satisfies "number"; bool never satisfies integer/number
    assert delivery.conformance_problems(3, {"type": "number"}) == []
    assert delivery.conformance_problems(True, {"type": "integer"}) != []


# ---- settlement receipt decoding ----------------------------------------------

def _receipt_b64(**kw):
    body = {"success": True, "transaction": "0x" + "ab" * 32,
            "network": "base", "payer": "0x" + "cd" * 20}
    body.update(kw)
    return base64.b64encode(json.dumps(body).encode()).decode()


def test_decode_settlement_base64_and_raw_json():
    obj, problems = delivery.decode_settlement(_receipt_b64())
    assert problems == [] and obj["success"] is True
    raw = json.dumps({"success": False, "txHash": "0x1"})
    obj, problems = delivery.decode_settlement(raw)
    assert problems == [] and obj["txHash"] == "0x1"


def test_decode_settlement_garbage_fails_soft():
    obj, problems = delivery.decode_settlement("!!not-anything!!")
    assert obj is None and problems
    obj, problems = delivery.decode_settlement(
        base64.b64encode(b"[1,2,3]").decode())
    assert obj is None and "not a JSON object" in problems[0]


def test_settlement_fields_both_dialects():
    f = delivery.settlement_fields({"transaction": "0xA", "network": "base",
                                    "payer": "0xB", "success": True})
    assert f == {"network": "base", "transaction": "0xA", "payer": "0xB",
                 "success": True}
    assert delivery.settlement_fields({"txHash": "0xC"})["transaction"] == "0xC"
    assert delivery.settlement_fields(None)["transaction"] is None


# ---- the delivery verdict rule ------------------------------------------------

@pytest.mark.parametrize(
    "supported,refuted,unverified,checked,valid,expect",
    [
        (2, 0, 1, False, None, "consistent"),      # supported, nothing refuted
        (0, 0, 0, True, True, "consistent"),       # schema validates, no claims
        (3, 1, 0, False, None, "degraded"),        # minority refuted
        (1, 1, 0, False, None, "inconsistent"),    # refuted matches supported
        (0, 2, 1, False, None, "inconsistent"),    # refuted dominates
        (5, 0, 0, True, False, "inconsistent"),    # shape violation wins
        (0, 0, 3, False, None, "unverifiable"),    # nothing confirmable
        (0, 0, 0, False, None, "unverifiable"),
    ])
def test_derive_verdict(supported, refuted, unverified, checked, valid, expect):
    verdict, rationale = delivery.derive_verdict(
        supported, refuted, unverified, checked, valid)
    assert verdict == expect and rationale


# ---- POST /extract -------------------------------------------------------------

def test_extract_returns_attested_claims(client):
    r = client.post("/extract", json={"text": DELIVERED_PROSE})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(body["claims"]) >= 1
    assert body["input_sha256"] == attest.sha256_text(DELIVERED_PROSE)
    assert body["attestation"]["attested"] is True
    v = attest.verify_attested_response("extract", body)
    assert v == {"valid": True, "problems": []}


def test_extract_receipt_catches_claim_tampering(client):
    body = client.post("/extract", json={"text": DELIVERED_PROSE}).json()
    body["claims"][0] = "The Eiffel Tower was completed in 2001."
    v = attest.verify_attested_response("extract", body)
    assert v["valid"] is False


def test_extract_is_deterministic(client):
    a = client.post("/extract", json={"text": DELIVERED_PROSE}).json()
    b = client.post("/extract", json={"text": DELIVERED_PROSE}).json()
    assert a["claims"] == b["claims"]


# ---- POST /attest-delivery ------------------------------------------------------

def test_delivery_schema_valid_yields_consistent(client):
    r = client.post("/attest-delivery", json={
        "service": "https://api.vendor.xyz/enrich",
        "response_text": DELIVERED_JSON,
        "payment_receipt": _receipt_b64(),
        "advertised_schema": {"type": "object", "required": ["figi", "name"]},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["conformance"] == {"checked": True, "valid": True, "problems": []}
    assert body["payment"]["bound"] is True
    assert body["payment"]["network"] == "base"
    assert body["payment"]["transaction"].startswith("0x")
    assert body["payment"]["receipt_sha256"] == attest.sha256_text(_receipt_b64())
    assert body["response_sha256"] == attest.sha256_text(DELIVERED_JSON)
    # stub retrieval can't refute anything, and the schema validates
    assert body["delivery_verdict"] in ("consistent", "unverifiable")
    if body["grounding"]["refuted"] == 0:
        assert body["delivery_verdict"] == "consistent"


def test_delivery_schema_violation_is_inconsistent(client):
    r = client.post("/attest-delivery", json={
        "service": "vendor",
        "response_text": DELIVERED_JSON,
        "advertised_schema": {"type": "object", "required": ["price_usd"]},
    })
    body = r.json()
    assert body["conformance"]["valid"] is False
    assert body["delivery_verdict"] == "inconsistent"
    assert "schema" in body["rationale"]


def test_delivery_non_json_payload_against_schema(client):
    r = client.post("/attest-delivery", json={
        "service": "vendor",
        "response_text": DELIVERED_PROSE,
        "advertised_schema": {"type": "object"},
    })
    body = r.json()
    assert body["conformance"]["valid"] is False
    assert body["delivery_verdict"] == "inconsistent"


def test_delivery_receipt_verifies_offline_and_catches_tampering(client):
    body = client.post("/attest-delivery", json={
        "service": "vendor", "response_text": DELIVERED_JSON,
        "payment_receipt": _receipt_b64(),
        "advertised_schema": {"type": "object", "required": ["figi"]},
    }).json()
    assert attest.verify_attested_response("delivery", body) == \
        {"valid": True, "problems": []}
    # flip the verdict a buyer would act on -> receipt must break
    tampered = json.loads(json.dumps(body))
    tampered["delivery_verdict"] = "consistent" \
        if body["delivery_verdict"] != "consistent" else "inconsistent"
    assert attest.verify_attested_response("delivery", tampered)["valid"] is False
    # swap the bound payment -> receipt must break
    tampered = json.loads(json.dumps(body))
    tampered["payment"]["transaction"] = "0x" + "99" * 32
    assert attest.verify_attested_response("delivery", tampered)["valid"] is False


def test_delivery_without_receipt_or_schema_still_answers(client):
    body = client.post("/attest-delivery", json={
        "service": "vendor", "response_text": DELIVERED_PROSE}).json()
    assert body["payment"]["bound"] is False
    assert body["conformance"]["checked"] is False
    assert body["delivery_verdict"] in ("consistent", "degraded",
                                        "inconsistent", "unverifiable")
    assert attest.verify_attested_response("delivery", body)["valid"] is True


# ---- x402 pricing of the new paths ----------------------------------------------

def _enable(monkeypatch, free_per_day=0):
    monkeypatch.setenv("GROUNDCHECK_X402_PAY_TO",
                       "0x1111111111111111111111111111111111111111")
    monkeypatch.setattr(app_mod.config, "X402_FREE_PER_DAY", free_per_day)


def test_new_paths_are_priced_and_402_when_unpaid(client, monkeypatch):
    _enable(monkeypatch)
    for path, payload in [
        ("/extract", {"text": DELIVERED_PROSE}),
        ("/attest-delivery", {"service": "v", "response_text": DELIVERED_PROSE}),
    ]:
        r = client.post(path, json=payload)
        assert r.status_code == 402, path
        assert r.json()["accepts"], path


def test_manifest_lists_new_resources_and_vocabulary(client, monkeypatch):
    _enable(monkeypatch)
    m = client.get("/.well-known/x402").json()
    paths = {res["path"] for res in m["resources"]}
    assert {"/check", "/resolve", "/extract", "/attest-delivery"} <= paths
    prices = {res["path"]: res["priceUSD"] for res in m["resources"]}
    assert prices["/extract"] == pytest.approx(0.005)
    assert prices["/attest-delivery"] == pytest.approx(0.05)
    assert "delivery-verification" in m["tags"]
    assert "agentic-commerce" in m["tags"]


def test_verify_stays_free_alongside_new_paid_paths(client, monkeypatch):
    _enable(monkeypatch)
    r = client.post("/verify", json={"claim": "The Eiffel Tower is in Paris."})
    assert r.status_code == 200


# ---- MCP surface -----------------------------------------------------------------

def _rpc(method, params=None, msg_id=1):
    return {"jsonrpc": "2.0", "id": msg_id, "method": method,
            "params": params or {}}


def test_mcp_lists_five_tools_with_prices(client, monkeypatch):
    _enable(monkeypatch)
    r = client.post("/mcp", json=_rpc("tools/list"))
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert set(tools) == {"verify_claim", "check_citations",
                          "resolve_instrument", "extract_claims",
                          "attest_delivery"}
    assert tools["attest_delivery"]["_meta"]["x402"]["price"]["amount"] == "0.050000"
    assert tools["extract_claims"]["_meta"]["x402"]["price"]["amount"] == "0.005000"
    assert "_meta" not in tools["verify_claim"]


def test_mcp_attest_delivery_tool_answers_when_free(client):
    r = client.post("/mcp", json=_rpc("tools/call", {
        "name": "attest_delivery",
        "arguments": {"service": "vendor", "response_text": DELIVERED_JSON,
                      "advertised_schema": {"type": "object",
                                            "required": ["figi"]}},
    }))
    assert r.status_code == 200
    payload = json.loads(r.json()["result"]["content"][0]["text"])
    assert payload["conformance"]["valid"] is True
    assert payload["attestation"]["attested"] is True


def test_mcp_extract_claims_tool(client):
    r = client.post("/mcp", json=_rpc("tools/call", {
        "name": "extract_claims", "arguments": {"text": DELIVERED_PROSE}}))
    payload = json.loads(r.json()["result"]["content"][0]["text"])
    assert payload["count"] >= 1 and payload["claims"]
