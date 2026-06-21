"""Tests for retrieval pure logic: HTML/entity stripping, backend selection
from env, and the stub source contract. No HTTP is exercised — the network
paths (_wikipedia/_custom) are intentionally not called."""
import importlib

import pytest

from groundcheck_engine import config
from groundcheck_engine.retrieval import Retriever, _strip_html


# ── _strip_html ────────────────────────────────────────────────────────────
def test_strip_removes_tags():
    assert _strip_html('<span class="x">Paris</span>') == "Paris"


def test_strip_decodes_named_and_numeric_entities():
    assert _strip_html("Tom&#039;s &quot;cat&quot; &amp; dog") == 'Tom\'s "cat" & dog'


def test_strip_handles_both_apostrophe_forms_and_nbsp():
    assert _strip_html("a&#39;b&apos;c&nbsp;d") == "a'b'c d"


def test_strip_trims_surrounding_whitespace():
    assert _strip_html("  <b>x</b>  ") == "x"


def test_strip_search_snippet_with_match_markup():
    # Mirrors a real Wikipedia search snippet shape.
    raw = 'The <span class="searchmatch">Eiffel</span> Tower &amp; its base'
    assert _strip_html(raw) == "The Eiffel Tower & its base"


# ── backend selection (env-driven) ─────────────────────────────────────────
def _reload_config(monkeypatch, **env):
    for k in ("GROUNDCHECK_SEARCH_BACKEND", "GROUNDCHECK_SEARCH_URL", "GROUNDCHECK_SEARCH_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(config)
    # retrieval reads config at __init__ time via the module reference, so reload
    # config and let Retriever() pick up the fresh values.
    import groundcheck_engine.retrieval as retr
    importlib.reload(retr)
    return retr.Retriever


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    # Reset modules to pristine env-free state after each test.
    importlib.reload(config)
    import groundcheck_engine.retrieval as retr
    importlib.reload(retr)


def test_default_backend_is_wikipedia(monkeypatch):
    R = _reload_config(monkeypatch)
    r = R()
    assert r.backend == "wikipedia"
    assert r.is_live is True


def test_stub_backend_disables_live(monkeypatch):
    R = _reload_config(monkeypatch, GROUNDCHECK_SEARCH_BACKEND="stub")
    r = R()
    assert r.backend == "stub"
    assert r.is_live is False


def test_custom_url_overrides_wikipedia(monkeypatch):
    R = _reload_config(monkeypatch, GROUNDCHECK_SEARCH_URL="https://search.example/api")
    r = R()
    assert r.backend == "custom"
    assert r.is_live is True


def test_stub_takes_precedence_over_custom_url(monkeypatch):
    R = _reload_config(
        monkeypatch,
        GROUNDCHECK_SEARCH_BACKEND="stub",
        GROUNDCHECK_SEARCH_URL="https://search.example/api",
    )
    assert R().backend == "stub"


# ── stub source contract ───────────────────────────────────────────────────
def test_stub_sources_are_marked_non_evidence(monkeypatch):
    R = _reload_config(monkeypatch, GROUNDCHECK_SEARCH_BACKEND="stub")
    sources = R()._stub("is the sky blue", 5)
    assert sources, "stub must still return placeholder sources"
    # Every stub source MUST carry stub=True and no stance, so the verdict
    # layer treats them as non-evidence and reports 'unverified'.
    assert all(s.stub is True for s in sources)
    assert all(s.stance is None for s in sources)


def test_stub_is_capped_at_two_sources(monkeypatch):
    R = _reload_config(monkeypatch, GROUNDCHECK_SEARCH_BACKEND="stub")
    r = R()
    assert len(r._stub("q", 5)) == 2   # min(2, n)
    assert len(r._stub("q", 1)) == 1


def test_stub_snippet_echoes_query(monkeypatch):
    R = _reload_config(monkeypatch, GROUNDCHECK_SEARCH_BACKEND="stub")
    [s, *_] = R()._stub("water is wet", 2)
    assert "water is wet" in s.snippet


@pytest.mark.asyncio
async def test_stub_search_routes_to_stub(monkeypatch):
    R = _reload_config(monkeypatch, GROUNDCHECK_SEARCH_BACKEND="stub")
    sources = await R().search("anything", max_sources=3)
    assert all(s.stub for s in sources)
