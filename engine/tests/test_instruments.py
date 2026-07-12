"""Instrument identity: /resolve enrichment + entity resolution inside /verify.

All offline — OpenFIGI is monkeypatched; these tests must pass with no network.
"""
import pytest
from fastapi.testclient import TestClient

from groundcheck_engine import app as app_module
from groundcheck_engine import instruments
from groundcheck_engine.app import app
from groundcheck_engine.models import Source

AAPL = {
    "figi": "BBG000B9XRY4", "name": "APPLE INC", "ticker": "AAPL",
    "exchCode": "US", "securityType": "Common Stock", "marketSector": "Equity",
    "compositeFIGI": "BBG000B9XRY4", "shareClassFIGI": "BBG001S5N8V8",
    "securityDescription": "AAPL",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any un-mocked OpenFIGI call is a test bug — make it loud."""
    async def boom(*a, **k):
        raise AssertionError("unexpected OpenFIGI network call")
    monkeypatch.setattr(instruments, "_mapping", boom)
    monkeypatch.setattr(instruments, "_search", boom)
    monkeypatch.setattr(instruments.config, "RESOLVE_CACHE_TTL_S", 0)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GROUNDCHECK_X402_PAY_TO", raising=False)
    return TestClient(app)


# ---- id-type detection ---------------------------------------------------------

def test_detect_id_type_shapes():
    assert instruments.detect_id_type("BBG000B9XRY4") == "ID_BB_GLOBAL"
    assert instruments.detect_id_type("US0378331005") == "ID_ISIN"
    assert instruments.detect_id_type("037833100") == "ID_CUSIP"
    assert instruments.detect_id_type("AAPL") == "TICKER"
    assert instruments.detect_id_type("Apple five percent bond") is None


def test_find_identifiers_is_conservative():
    text = ("$AAPL rallied while NYSE:IBM fell; ISIN US0378331005 was busy. "
            "Apple pie sales and the number 123456789 are not instruments.")
    refs = instruments.find_identifiers(text)
    assert ("TICKER", "AAPL") in refs
    assert ("TICKER", "IBM") in refs
    assert ("ID_ISIN", "US0378331005") in refs
    # bare company names and 9-digit numbers must NOT be extracted
    assert all(v not in ("APPLE", "123456789") for _, v in refs)


# ---- /resolve ------------------------------------------------------------------

def test_resolve_maps_ticker_with_provenance(client, monkeypatch):
    async def fake_mapping(id_type, value):
        assert (id_type, value) == ("TICKER", "AAPL")
        return [AAPL]
    monkeypatch.setattr(instruments, "_mapping", fake_mapping)

    r = client.post("/resolve", json={"query": "AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True and body["id_type"] == "TICKER"
    inst = body["instruments"][0]
    assert inst["figi"] == "BBG000B9XRY4" and inst["market_sector"] == "Equity"
    prov = body["provenance"]
    assert "OpenFIGI" in prov["source"] and prov["url"].endswith("/v3")
    assert prov["retrieved_at"]


def test_resolve_falls_back_to_search_for_names(client, monkeypatch):
    async def fake_search(query, max_results):
        assert query == "Apple common stock"
        return [AAPL]
    monkeypatch.setattr(instruments, "_search", fake_search)

    r = client.post("/resolve", json={"query": "Apple common stock"})
    assert r.status_code == 200
    assert r.json()["matched"] is True and r.json()["id_type"] is None


def test_resolve_not_found_is_honest(client, monkeypatch):
    async def fake_mapping(id_type, value):
        return []
    async def fake_search(query, max_results):
        return []
    monkeypatch.setattr(instruments, "_mapping", fake_mapping)
    monkeypatch.setattr(instruments, "_search", fake_search)

    r = client.post("/resolve", json={"query": "ZZZZNOTREAL"})
    body = r.json()
    assert body["matched"] is False and body["instruments"] == []
    assert "no instrument found" in body["note"]


def test_resolve_rejects_unknown_id_type(client):
    r = client.post("/resolve", json={"query": "AAPL", "id_type": "ID_MADE_UP"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is False and "unknown id_type" in body["note"]


def test_resolve_survives_openfigi_outage(client, monkeypatch):
    async def down(*a, **k):
        import httpx
        raise httpx.ConnectError("boom")
    monkeypatch.setattr(instruments, "_mapping", down)

    r = client.post("/resolve", json={"query": "AAPL"})
    body = r.json()
    assert r.status_code == 200 and body["matched"] is False
    assert "unreachable" in body["note"]


def test_resolve_is_priced_for_x402():
    from groundcheck_engine import config, x402
    assert "/resolve" in config.X402_PRICES_USD
    assert x402._BAZAAR_EXTENSIONS["/resolve"]["bazaar"]["routeTemplate"] == "/resolve"


# ---- entity resolution inside /verify ------------------------------------------

def _stub_pipeline(monkeypatch):
    async def fake_search_and_classify(claim, max_sources):
        return ([Source(title="t", url="https://x", snippet="s", stance="neutral")],
                "stub")
    monkeypatch.setattr(app_module, "_search_and_classify", fake_search_and_classify)
    monkeypatch.setattr(app_module.config, "CACHE_TTL_S", 0)


def test_verify_resolves_cashtag(client, monkeypatch):
    _stub_pipeline(monkeypatch)

    async def fake_mapping(id_type, value):
        return [AAPL] if value == "AAPL" else []
    monkeypatch.setattr(instruments, "_mapping", fake_mapping)

    r = client.post("/verify", json={"claim": "$AAPL closed above $200 in June 2026."})
    assert r.status_code == 200
    insts = r.json()["instruments"]
    assert len(insts) == 1
    assert insts[0]["resolved"] is True
    assert insts[0]["instrument"]["figi"] == "BBG000B9XRY4"


def test_verify_flags_unresolvable_identifier(client, monkeypatch):
    _stub_pipeline(monkeypatch)

    async def fake_mapping(id_type, value):
        return []
    monkeypatch.setattr(instruments, "_mapping", fake_mapping)

    r = client.post("/verify", json={"claim": "ISIN US9999999999 pays a 5% coupon."})
    body = r.json()
    assert body["instruments"][0]["resolved"] is False
    assert "does not resolve in open symbology" in body["rationale"]


def test_verify_without_identifiers_makes_no_openfigi_call(client, monkeypatch):
    _stub_pipeline(monkeypatch)  # no_network fixture stays armed — any call raises
    r = client.post("/verify", json={"claim": "The Eiffel Tower is in Paris."})
    assert r.status_code == 200
    assert r.json()["instruments"] == []
