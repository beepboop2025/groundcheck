"""Tests for the provider registry and default ordering policy (pure, env-only)."""
import pytest

from free_llm_router import REGISTRY, available_providers, default_order
from free_llm_router.providers import Provider
from free_llm_router.router import ProviderStats


_KEY_ENVS = [
    "GROQ_API_KEY", "CEREBRAS_API_KEY", "GOOGLE_AI_STUDIO_API_KEY",
    "MISTRAL_API_KEY", "OPENROUTER_API_KEY",
]


@pytest.fixture(autouse=True)
def _clear_keys(monkeypatch):
    for env in _KEY_ENVS:
        monkeypatch.delenv(env, raising=False)


def test_registry_is_nonempty_and_well_formed():
    assert REGISTRY
    for p in REGISTRY:
        assert p.models.get("fast")
        assert p.models.get("smart")
        assert p.rpm >= 1
        assert p.base_url.startswith("https://")


def test_priorities_are_unique():
    prios = [p.priority for p in REGISTRY]
    assert len(prios) == len(set(prios))


def test_available_providers_empty_without_keys():
    assert available_providers() == []


def test_available_providers_picks_up_set_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    names = [p.name for p in available_providers()]
    assert names == ["groq"]


def test_api_key_property_reads_env(monkeypatch):
    groq = next(p for p in REGISTRY if p.name == "groq")
    assert groq.api_key is None
    monkeypatch.setenv("GROQ_API_KEY", "sk-xyz")
    assert groq.api_key == "sk-xyz"


def test_model_for_resolves_tier():
    groq = next(p for p in REGISTRY if p.name == "groq")
    assert groq.model_for("fast") == "llama-3.1-8b-instant"
    assert groq.model_for("nonexistent") is None


def _provider(name, priority):
    return Provider(
        name=name, base_url="https://x/v1", api_key_env="X",
        models={"fast": "m", "smart": "m"}, rpm=10, rpd=None, priority=priority,
    )


def _stats(p):
    return ProviderStats(
        provider=p, circuit_state="closed", tokens_available=True,
        day_count=0, day_limit=None, last_latency_ms=0.0,
    )


def test_default_order_sorts_by_priority_ascending():
    a = _provider("a", priority=50)
    b = _provider("b", priority=10)
    c = _provider("c", priority=30)
    ordered = default_order([_stats(a), _stats(b), _stats(c)])
    assert [p.name for p in ordered] == ["b", "c", "a"]


def test_default_order_ignores_health_signals():
    # default_order is priority-only by contract; circuit/tokens do not reorder.
    hi = _provider("hi", priority=10)
    lo = _provider("lo", priority=20)
    s_hi = ProviderStats(provider=hi, circuit_state="open", tokens_available=False,
                         day_count=999, day_limit=1, last_latency_ms=9999.0)
    s_lo = _stats(lo)
    ordered = default_order([s_lo, s_hi])
    assert [p.name for p in ordered] == ["hi", "lo"]
