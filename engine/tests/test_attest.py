"""Response attestation: portable Ed25519 receipts on /verify and /check.

No filesystem, no network: keys come from GROUNDCHECK_ATTEST_KEY (or are
ephemeral), and the retriever runs in stub mode. What's under test: receipts
round-trip, tampering is caught, manifests are deterministic, endpoints carry
the attestation field, the pubkey endpoint documents verification, and a
signing failure never breaks a verdict.
"""
import hashlib
import json
import os

os.environ["GROUNDCHECK_SEARCH_BACKEND"] = "stub"  # before app import

import pytest
from fastapi.testclient import TestClient

from groundcheck_engine import app as app_mod
from groundcheck_engine import attest

SEED = "11" * 32  # deterministic test key (never use a fixed seed in prod)
CLAIM = "The Eiffel Tower was completed in 1889."
TEXT = ("The Eiffel Tower is located in Paris and was completed in 1889. "
        "It was the tallest man-made structure in the world for 41 years.")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GROUNDCHECK_X402_PAY_TO", raising=False)
    monkeypatch.setenv(attest.ENV_KEY, SEED)
    app_mod._free_used.clear()
    app_mod._hits.clear()
    app_mod._verdict_cache.clear()
    return TestClient(app_mod.app)


def _sample_manifest():
    return attest.build_verify_manifest(
        {"claim": CLAIM, "verdict": "supported", "confidence": 0.8,
         "sources": [{"url": "https://en.wikipedia.org/wiki/Eiffel_Tower",
                      "title": "", "snippet": ""}],
         "classifier": "free-llm-router", "backend": "wikipedia"},
        "2026-07-13T00:00:00+00:00")


# ---- receipt round trip and tamper detection ---------------------------------

def test_receipt_round_trip(monkeypatch):
    monkeypatch.setenv(attest.ENV_KEY, SEED)
    m = _sample_manifest()
    receipt = attest.sign_receipt("verify", m)
    assert receipt["algo"] == "ed25519"
    assert receipt["domain"] == "groundcheck-attest-v1"
    assert receipt["signed_at"] == m["signed_at"]
    v = attest.verify_receipt("verify", m, receipt)
    assert v == {"valid": True, "problems": []}


def test_tampered_manifest_fails(monkeypatch):
    monkeypatch.setenv(attest.ENV_KEY, SEED)
    m = _sample_manifest()
    receipt = attest.sign_receipt("verify", m)
    m["verdict"] = "refuted"
    v = attest.verify_receipt("verify", m, receipt)
    assert not v["valid"]
    assert any("manifest" in p for p in v["problems"])


def test_tampered_signature_fails(monkeypatch):
    monkeypatch.setenv(attest.ENV_KEY, SEED)
    m = _sample_manifest()
    receipt = attest.sign_receipt("verify", m)
    receipt["sig"] = "00" * 64
    assert not attest.verify_receipt("verify", m, receipt)["valid"]


def test_wrong_kind_is_domain_separated(monkeypatch):
    # A receipt for one kind must never verify as another kind.
    monkeypatch.setenv(attest.ENV_KEY, SEED)
    m = _sample_manifest()
    receipt = attest.sign_receipt("verify", m)
    assert not attest.verify_receipt("check", m, receipt)["valid"]


# ---- determinism --------------------------------------------------------------

def test_canonical_hash_is_key_order_independent():
    a = {"b": 1, "a": [1, 2], "c": {"y": 0.5, "x": "s"}}
    b = {"c": {"x": "s", "y": 0.5}, "a": [1, 2], "b": 1}
    assert attest.canonical_hash(a) == attest.canonical_hash(b)


def test_same_input_same_manifest_hash(monkeypatch):
    monkeypatch.setenv(attest.ENV_KEY, SEED)
    r1 = attest.sign_receipt("verify", _sample_manifest())
    r2 = attest.sign_receipt("verify", _sample_manifest())
    assert r1["manifest_hash"] == r2["manifest_hash"]
    assert r1["sig"] == r2["sig"]  # Ed25519 is deterministic too


# ---- key modes ------------------------------------------------------------------

def test_env_seed_gives_persistent_stable_key(monkeypatch):
    monkeypatch.setenv(attest.ENV_KEY, SEED)
    assert attest.key_mode() == "persistent"
    assert attest.public_key_hex() == attest.public_key_hex()


def test_no_env_gives_ephemeral_key(monkeypatch):
    monkeypatch.delenv(attest.ENV_KEY, raising=False)
    assert attest.key_mode() == "ephemeral"
    # stable within the process: two receipts share one ephemeral identity
    r1 = attest.sign_receipt("verify", _sample_manifest())
    r2 = attest.sign_receipt("verify", _sample_manifest())
    assert r1["public_key"] == r2["public_key"]


def test_malformed_env_seed_refuses_to_sign(monkeypatch):
    monkeypatch.setenv(attest.ENV_KEY, "not-hex")
    with pytest.raises(ValueError):
        attest.sign_receipt("verify", _sample_manifest())


def test_generate_key_yields_usable_seed(monkeypatch):
    seed = attest.generate_key()
    assert len(seed) == 64 and int(seed, 16) >= 0
    monkeypatch.setenv(attest.ENV_KEY, seed)
    m = _sample_manifest()
    assert attest.verify_receipt("verify", m, attest.sign_receipt("verify", m))["valid"]


# ---- endpoints -------------------------------------------------------------------

def test_verify_endpoint_returns_valid_attestation(client):
    r = client.post("/verify", json={"claim": CLAIM})
    assert r.status_code == 200
    body = r.json()
    att = body["attestation"]
    assert att["attested"] is True
    # the manifest now also binds the evidence path (evidence_root) and the
    # model route (route_hash) — the provenance upgrade
    assert set(att["manifest_keys"]) == {"backend", "claim_sha256", "confidence",
                                         "evidence_root", "route_hash",
                                         "model", "signed_at", "source_urls",
                                         "verdict"}
    assert attest.verify_attested_response("verify", body)["valid"]


def test_verify_receipt_rebuilds_from_response_alone(client):
    # Exactly what a third party does: response JSON in, cryptography out,
    # including the evidence-path and route commitments.
    from groundcheck_engine import provenance
    body = client.post("/verify", json={"claim": CLAIM}).json()
    receipt = body["attestation"]["receipt"]
    m = {"claim_sha256": hashlib.sha256(body["claim"].encode()).hexdigest(),
         "verdict": body["verdict"], "confidence": body["confidence"],
         "source_urls": [s["url"] for s in body["sources"]],
         "evidence_root": provenance.recompute_evidence_root(body),
         "route_hash": provenance.route_hash(body),
         "model": body["classifier"], "backend": body["backend"],
         "signed_at": receipt["signed_at"]}
    h = hashlib.sha256(json.dumps(m, sort_keys=True,
                                  separators=(",", ":")).encode()).hexdigest()
    assert h == receipt["manifest_hash"]
    assert attest.verify_receipt("verify", m, receipt)["valid"]
    # and the recomputed evidence root matches the one the response advertises
    assert m["evidence_root"] == body["provenance"]["evidence_root"]


def test_tampered_response_detected_end_to_end(client):
    body = client.post("/verify", json={"claim": CLAIM}).json()
    body["verdict"] = "supported" if body["verdict"] != "supported" else "refuted"
    assert not attest.verify_attested_response("verify", body)["valid"]


def test_cache_hit_reserves_original_receipt(client):
    first = client.post("/verify", json={"claim": CLAIM})
    second = client.post("/verify", json={"claim": CLAIM})
    assert second.headers["X-Groundcheck-Cache"] == "hit"
    assert second.json()["attestation"] == first.json()["attestation"]


def test_check_endpoint_returns_valid_attestation(client):
    r = client.post("/check", json={"text": TEXT, "max_claims": 2})
    assert r.status_code == 200
    body = r.json()
    att = body["attestation"]
    assert att["attested"] is True
    assert att["input_sha256"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert attest.verify_attested_response("check", body)["valid"]


def test_mcp_verify_claim_carries_attestation(client):
    r = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "verify_claim", "arguments": {"claim": CLAIM}}})
    assert r.status_code == 200
    payload = json.loads(r.json()["result"]["content"][0]["text"])
    assert payload["attestation"]["attested"] is True
    assert attest.verify_attested_response("verify", payload)["valid"]


def test_pubkey_endpoint(client):
    r = client.get("/attest/pubkey")
    assert r.status_code == 200
    body = r.json()
    assert body["public_key"] == attest.public_key_hex()
    assert body["algo"] == "ed25519"
    assert body["domain"] == "groundcheck-attest-v1"
    assert body["key_mode"] == "persistent"
    assert "<kind>" in body["message_format"]
    assert "Ed25519PublicKey" in body["verify_example"]
    assert "verify" in body["manifests"] and "check" in body["manifests"]
    assert "warning" not in body


def test_pubkey_endpoint_flags_ephemeral_mode(client, monkeypatch):
    monkeypatch.delenv(attest.ENV_KEY, raising=False)
    body = client.get("/attest/pubkey").json()
    assert body["key_mode"] == "ephemeral"
    assert "warning" in body


# ---- signing failure never breaks the endpoint -----------------------------------

def test_signing_failure_does_not_break_verify(client, monkeypatch):
    def boom(kind, manifest):
        raise RuntimeError("hsm on fire")
    monkeypatch.setattr(attest, "sign_receipt", boom)
    r = client.post("/verify", json={"claim": "Signing is down but verdicts flow."})
    assert r.status_code == 200
    att = r.json()["attestation"]
    assert att["attested"] is False
    assert "hsm on fire" in att["reason"]


def test_signing_failure_does_not_break_check(client, monkeypatch):
    monkeypatch.setattr(attest, "sign_receipt",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    r = client.post("/check", json={"text": TEXT, "max_claims": 2})
    assert r.status_code == 200
    assert r.json()["attestation"]["attested"] is False
