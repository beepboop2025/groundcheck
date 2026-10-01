"""Primary-source numeric matching, exact dates, and outage behavior."""
import httpx
import pytest

from groundcheck_engine import app as app_mod, official_rates


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("sentence", [
    "SOFR was 3.88% on 2026-09-29.",
    "SOFR on 29 Sep 2026 was 3.88%.",
    "On September 29, 2026, the SOFR rate was 3.88%.",
])
def test_explicit_atomic_dates(sentence):
    parsed = official_rates.parse_claim(sentence)
    assert parsed.rate == "SOFR" and parsed.day == "2026-09-29"
    assert str(parsed.value) == "3.88"


@pytest.mark.parametrize("sentence", [
    "SOFR was 3.88% on 29 Sep.", "SOFR was 3.88% today.",
    "SOFR was 3.88% on 2026-02-30.",
    "SOFR was 3.88% on 2026-09-29 and markets were calm.",
    "SOFR was 3.88% on 2099-09-29.",
])
def test_ambiguous_invalid_future_or_compound_claim_is_not_numeric_match(sentence):
    assert official_rates.parse_claim(sentence) is None


def mocked_client(monkeypatch, rows):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"refRates": rows}))
    monkeypatch.setattr(official_rates.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))


@pytest.mark.anyio
@pytest.mark.parametrize("value,stance", [("3.88", "supports"), ("3.89", "refutes")])
async def test_exact_record_and_direct_numeric_stance(monkeypatch, value, stance):
    mocked_client(monkeypatch, [{"type": "SOFR", "effectiveDate": "2026-09-29", "percentRate": 3.88}])
    sources = await official_rates.compare(official_rates.parse_claim(f"SOFR was {value}% on 2026-09-29."))
    assert sources[0].stance == stance
    assert "startDate=2026-09-29" in sources[0].url
    assert "response SHA-256" in sources[0].snippet


@pytest.mark.anyio
@pytest.mark.parametrize("rows", [[],
    [{"type": "SOFR", "effectiveDate": "2026-09-28", "percentRate": 3.88}],
    [{"type": "SOFR", "effectiveDate": "2026-09-29", "percentRate": True}],
    [{"type": "SOFR", "effectiveDate": "2026-09-29", "percentRate": 3.88}] * 2,
])
async def test_missing_wrong_date_boolean_and_duplicate_prints_fail_closed(monkeypatch, rows):
    mocked_client(monkeypatch, rows)
    with pytest.raises(ValueError):
        await official_rates.compare(official_rates.parse_claim("SOFR was 3.88% on 2026-09-29."))


@pytest.mark.anyio
async def test_official_outage_does_not_fall_back_to_wikipedia_or_cache(monkeypatch):
    monkeypatch.setattr(app_mod.retriever, "backend", "wikipedia+gdelt")
    async def unavailable(reference):
        raise httpx.ConnectError("source unavailable")
    async def forbidden(*args):
        raise AssertionError("unrelated search fallback was invoked")
    monkeypatch.setattr(official_rates, "compare", unavailable)
    monkeypatch.setattr(app_mod.retriever, "search", forbidden)
    monkeypatch.setattr(app_mod, "_resolve_claim_instruments", lambda claim: no_instruments())
    result, cacheable = await app_mod._verify_atomic("SOFR was 3.88% on 2026-09-29.", 5)
    assert result.verdict == "unverified" and result.sources == [] and cacheable is False


async def no_instruments():
    return []


@pytest.mark.anyio
async def test_partial_official_outage_in_compound_claim_is_not_cached(monkeypatch):
    async def classify(claim, maximum):
        if "SOFR" in claim:
            return [], "official-source-unavailable", None
        return [], "none", None
    monkeypatch.setattr(app_mod, "_search_and_classify", classify)
    result, cacheable = await app_mod._verify_compound(
        "A benchmark and a separate claim", ["SOFR claim", "Separate claim"], 5)
    assert len(result.atoms) == 2 and cacheable is False
