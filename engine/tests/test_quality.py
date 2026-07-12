"""v0.3 quality layer: claim extraction, verdict cache, retrieval fan-out merge."""

import os
import time

os.environ["GROUNDCHECK_SEARCH_BACKEND"] = "stub"  # before app import

import pytest
from fastapi.testclient import TestClient

from groundcheck_engine import app as app_mod
from groundcheck_engine.models import Source
from groundcheck_engine.retrieval import Retriever


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("GROUNDCHECK_X402_PAY_TO", raising=False)
    app_mod._verdict_cache.clear()
    app_mod._hits.clear()
    return TestClient(app_mod.app)


# ---- claim extraction ---------------------------------------------------------

def test_extraction_keeps_factual_sentences():
    text = ("The Eiffel Tower was completed in 1889. It is located in Paris, France. "
            "Napoleon Bonaparte was born in Corsica in 1769.")
    claims = app_mod._extract_claims(text, 8)
    assert len(claims) == 3


def test_extraction_skips_questions_opinions_and_dupes():
    text = ("Is the Eiffel Tower really the best monument in the world today? "
            "I think Paris is absolutely wonderful in the spring season. "
            "The Eiffel Tower was completed in 1889. "
            "The Eiffel Tower was completed in 1889. "
            "Beautiful and stirring, magnificent, wonderful, timeless, poetic.")
    claims = app_mod._extract_claims(text, 8)
    assert claims == ["The Eiffel Tower was completed in 1889."]


def test_extraction_respects_cap():
    text = " ".join(f"The tower number {i} was completed in {1800 + i}." for i in range(30))
    assert len(app_mod._extract_claims(text, 5)) == 5


# ---- verdict cache ------------------------------------------------------------

def test_verify_cache_hit_on_repeat(client):
    body = {"claim": "The Eiffel Tower is located in Paris, France."}
    r1 = client.post("/verify", json=body)
    assert r1.headers["X-Groundcheck-Cache"] == "miss"
    r2 = client.post("/verify", json=body)
    assert r2.headers["X-Groundcheck-Cache"] == "hit"
    assert r2.json() == r1.json()


def test_cache_expires(client, monkeypatch):
    body = {"claim": "The Eiffel Tower is located in Paris, France."}
    client.post("/verify", json=body)
    # age every entry past its TTL
    for k, (exp, v) in list(app_mod._verdict_cache.items()):
        app_mod._verdict_cache[k] = (time.time() - 1, v)
    r = client.post("/verify", json=body)
    assert r.headers["X-Groundcheck-Cache"] == "miss"


def test_check_reports_cache_hits(client):
    text = "The Eiffel Tower was completed in 1889. Napoleon Bonaparte was born in 1769."
    r1 = client.post("/check", json={"text": text})
    assert r1.headers["X-Groundcheck-Cache-Hits"] == "0"
    r2 = client.post("/check", json={"text": text})
    assert r2.headers["X-Groundcheck-Cache-Hits"] == "2"
    assert r2.json()["checked"] == 2


# ---- retrieval fan-out merge ---------------------------------------------------

def _src(title, url):
    return Source(title=title, url=url, snippet=title, stance=None)


@pytest.mark.anyio
async def test_live_merge_dedupes_and_caps(monkeypatch):
    r = Retriever()
    r.backend = "wikipedia+gdelt"

    async def fake_wiki(query, n):
        return [_src(f"wiki {i}", f"https://w/{i}") for i in range(n)]

    async def fake_news(query, n):
        return [_src("news 0", "https://n/0"), _src("wiki 0", "https://w/0")]

    monkeypatch.setattr(r, "_wikipedia", fake_wiki)
    monkeypatch.setattr(r, "_gdelt", fake_news)
    out = await r.search("q", 5)
    assert len(out) == 5
    urls = [s.url for s in out]
    assert len(set(urls)) == 5          # deduped
    assert "https://n/0" in urls        # news made it in


@pytest.mark.anyio
async def test_live_survives_news_backend_failure(monkeypatch):
    r = Retriever()
    r.backend = "wikipedia+gdelt"

    async def fake_wiki(query, n):
        return [_src(f"wiki {i}", f"https://w/{i}") for i in range(3)]

    async def broken_news(query, n):
        raise RuntimeError("gdelt down")

    monkeypatch.setattr(r, "_wikipedia", fake_wiki)
    monkeypatch.setattr(r, "_gdelt", broken_news)
    out = await r.search("q", 5)
    assert [s.url for s in out] == ["https://w/0", "https://w/1", "https://w/2"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
