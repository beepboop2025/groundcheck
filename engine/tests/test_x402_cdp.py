"""CDP mainnet facilitator: URL selection + per-request JWT auth.

The cdp-sdk JWT generator is patched via the x402._cdp_jwt wrapper (it is
lazily imported precisely so cdp-sdk stays an optional, CDP-only dependency
and the rest of the suite never needs it).
"""

import os

os.environ["GROUNDCHECK_SEARCH_BACKEND"] = "stub"

import pytest

from groundcheck_engine import x402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("GROUNDCHECK_X402_CDP_KEY_ID", "GROUNDCHECK_X402_CDP_KEY_SECRET",
              "GROUNDCHECK_X402_FACILITATOR", "GROUNDCHECK_X402_FACILITATOR_AUTH"):
        monkeypatch.delenv(k, raising=False)
    yield


def _enable_cdp(monkeypatch):
    monkeypatch.setenv("GROUNDCHECK_X402_CDP_KEY_ID", "040ac7c9-key")
    monkeypatch.setenv("GROUNDCHECK_X402_CDP_KEY_SECRET", "ed25519secret==")


# ---- facilitator URL selection ------------------------------------------------

def test_default_facilitator_is_testnet(clean_env):
    assert x402._facilitator_url() == "https://x402.org/facilitator"
    assert x402.cdp_enabled() is False


def test_cdp_keys_switch_to_cdp_facilitator(monkeypatch):
    _enable_cdp(monkeypatch)
    assert x402.cdp_enabled() is True
    assert x402._facilitator_url() == "https://api.cdp.coinbase.com/platform/v2/x402"


def test_explicit_facilitator_overrides_cdp(monkeypatch):
    _enable_cdp(monkeypatch)
    monkeypatch.setenv("GROUNDCHECK_X402_FACILITATOR", "https://my.facilitator")
    assert x402._facilitator_url() == "https://my.facilitator"


# ---- auth header selection ----------------------------------------------------

def test_no_auth_header_on_bare_testnet(clean_env):
    assert x402._facilitator_auth("POST", "https://x402.org/facilitator/verify") == {}


def test_static_auth_header_when_configured(monkeypatch):
    monkeypatch.setenv("GROUNDCHECK_X402_FACILITATOR_AUTH", "Bearer static-token")
    got = x402._facilitator_auth("POST", "https://f/verify")
    assert got == {"Authorization": "Bearer static-token"}


def test_cdp_jwt_is_per_request_and_path_bound(monkeypatch):
    _enable_cdp(monkeypatch)
    calls = []

    def fake_jwt(method, host, req_path):
        calls.append((method, host, req_path))
        return f"jwt-for-{req_path}"

    monkeypatch.setattr(x402, "_cdp_jwt", fake_jwt)
    verify_hdr = x402._facilitator_auth(
        "POST", "https://api.cdp.coinbase.com/platform/v2/x402/verify")
    settle_hdr = x402._facilitator_auth(
        "POST", "https://api.cdp.coinbase.com/platform/v2/x402/settle")
    # host+path parsed correctly, and each call gets its own token
    assert calls[0] == ("POST", "api.cdp.coinbase.com", "/platform/v2/x402/verify")
    assert calls[1] == ("POST", "api.cdp.coinbase.com", "/platform/v2/x402/settle")
    assert verify_hdr["Authorization"] == "Bearer jwt-for-/platform/v2/x402/verify"
    assert settle_hdr["Authorization"] != verify_hdr["Authorization"]


def test_facilitator_post_signs_with_cdp_when_enabled(monkeypatch):
    _enable_cdp(monkeypatch)
    seen = {}

    def fake_jwt(method, host, req_path):
        return "signed"

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"isValid": True}

    def fake_post(url, json, headers, timeout):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(x402, "_cdp_jwt", fake_jwt)
    monkeypatch.setattr(x402.httpx, "post", fake_post)
    out = x402._facilitator_post("/verify", {"x402Version": 1})
    assert out == {"isValid": True}
    assert seen["url"] == "https://api.cdp.coinbase.com/platform/v2/x402/verify"
    assert seen["auth"] == "Bearer signed"


# ---- description length guard (CDP rejects > 500 chars) -----------------------

def test_requirements_description_within_cdp_limit(monkeypatch):
    monkeypatch.setenv("GROUNDCHECK_X402_PAY_TO", "0xdead")
    reqs = x402._requirements("/check", "https://groundcheck.seiche.info/check", "base")
    assert len(reqs["description"]) <= 500
    assert "grounding" in reqs["description"].lower()
