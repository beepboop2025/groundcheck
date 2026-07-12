"""Stance classification via the canonical free-llm-router Python twin.

Honest degradation: if the router can't be imported or no provider key is set, stances
stay None and the verdict falls back to "unverified" — it never guesses.
"""
import json
import re
import sys
from typing import List, Optional, Tuple

from . import config
from .models import Source

_VALID = {"supports", "refutes", "neutral"}
_router = None  # lazily constructed FreeLLMRouter


def _load_router() -> Tuple[Optional[type], Optional[object]]:
    """Return (FreeLLMRouter, available_providers) or (None, None) if unavailable."""
    try:
        from free_llm_router import FreeLLMRouter, available_providers
        return FreeLLMRouter, available_providers
    except ImportError:
        if config.ROUTER_PATH and config.ROUTER_PATH not in sys.path:
            sys.path.insert(0, config.ROUTER_PATH)
        try:
            from free_llm_router import FreeLLMRouter, available_providers
            return FreeLLMRouter, available_providers
        except ImportError:
            return None, None


def _parse(text: str) -> dict:
    match = re.search(r"\[.*\]", text, re.S)  # first JSON array in the reply
    if not match:
        return {}
    try:
        arr = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    out = {}
    for obj in arr:
        if isinstance(obj, dict) and isinstance(obj.get("i"), int):
            out[obj["i"]] = str(obj.get("stance", "")).lower()
    return out


async def classify_stances(claim: str, sources: List[Source]) -> str:
    """Mutate `sources` in place, setting `.stance`. Returns which classifier ran."""
    global _router
    evidence = [s for s in sources if not s.stub]
    if not evidence:
        return "none"

    FreeLLMRouter, available_providers = _load_router()
    if FreeLLMRouter is None:
        return "unavailable"
    if not available_providers():
        return "no-providers"
    if _router is None:
        _router = FreeLLMRouter()

    numbered = "\n".join(f"[{i}] {s.title} — {s.snippet}" for i, s in enumerate(evidence))
    messages = [
        {
            "role": "system",
            "content": (
                "You judge whether each source supports, refutes, or is neutral toward a "
                "factual claim. Judge ONLY from the snippet text shown for that source — do not "
                "use outside knowledge and do not infer beyond what the snippet states. If a "
                "snippet does not directly address the claim, its stance is 'neutral'; reserve "
                "'supports'/'refutes' for snippets whose own words bear on the claim. "
                'Reply with ONLY a compact JSON array like '
                '[{"i":0,"stance":"supports"}]. stance is exactly one of: supports, refutes, '
                "neutral. Include every source index."
            ),
        },
        {"role": "user", "content": f"Claim: {claim}\n\nSources:\n{numbered}"},
    ]

    try:
        result = await _router.chat_completion(messages, task_type="classification", temperature=0.0)
    except Exception:  # noqa: BLE001 — any provider failure degrades honestly
        return "error"

    parsed = _parse(result.get("text", ""))
    for i, source in enumerate(evidence):
        if parsed.get(i) in _VALID:
            source.stance = parsed[i]
    return "free-llm-router"
