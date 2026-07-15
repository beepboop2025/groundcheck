"""Multi-provider stance panel — several free-tier models judge independently.

Why a panel: different model families disagree on *which* claims they get wrong
(arXiv:2602.01285), so a small weighted ensemble of free models beats any one of
them, and its combined score is what the conformal layer calibrates.

Each available provider becomes one panelist with its own pinned router (so the
failover machinery can't collapse the panel onto a single provider). Panelists
run concurrently and return:
  - per-source stances (supports / refutes / neutral), combined by majority vote
  - a verbalized probability p in [0,1] that the claim is true GIVEN ONLY the
    snippets shown, combined as a weighted mean (weights from calibration).

Honest degradation, in order: no router import -> "unavailable"; no provider
keys -> "no-providers"; every panelist errors -> "error". A one-provider panel
still works — it just is not an ensemble and is labeled accordingly.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import Dict, List, Optional, Tuple

from . import config
from .models import Source

_VALID = {"supports", "refutes", "neutral"}
_panel: Optional[List[Tuple[str, object]]] = None  # [(provider_name, pinned_router)]


def _load_router_module():
    try:
        import free_llm_router
        return free_llm_router
    except ImportError:
        if config.ROUTER_PATH and config.ROUTER_PATH not in sys.path:
            sys.path.insert(0, config.ROUTER_PATH)
        try:
            import free_llm_router
            return free_llm_router
        except ImportError:
            return None


def _build_panel() -> Optional[List[Tuple[str, object]]]:
    """One pinned router per available provider, best-priority first."""
    global _panel
    if _panel is not None:
        return _panel
    mod = _load_router_module()
    if mod is None:
        return None
    providers = mod.available_providers()
    if not providers:
        _panel = []
        return _panel
    providers = sorted(providers, key=lambda p: p.priority)[: config.ENSEMBLE_MAX]
    _panel = [(p.name, mod.FreeLLMRouter(providers=[p])) for p in providers]
    return _panel


def reset_panel() -> None:
    """Test hook: rebuild the panel on next call."""
    global _panel
    _panel = None


def _prompt(claim: str, evidence: List[Source]) -> List[Dict[str, str]]:
    numbered = "\n".join(f"[{i}] {s.title} — {s.snippet}" for i, s in enumerate(evidence))
    return [
        {
            "role": "system",
            "content": (
                "You judge whether each source supports, refutes, or is neutral toward a "
                "factual claim. Judge ONLY from the snippet text shown for that source — do not "
                "use outside knowledge and do not infer beyond what the snippet states. If a "
                "snippet does not directly address the claim, its stance is 'neutral'; reserve "
                "'supports'/'refutes' for snippets whose own words bear on the claim. "
                "Also estimate p, the probability that the claim is true judged ONLY from these "
                "snippets taken together: near 1 if they clearly establish it, near 0 if they "
                "clearly contradict it, near 0.5 if they are insufficient or conflicting. "
                'Reply with ONLY compact JSON like '
                '{"stances":[{"i":0,"stance":"supports"}],"p":0.85}. '
                "stance is exactly one of: supports, refutes, neutral. Include every source index."
            ),
        },
        {"role": "user", "content": f"Claim: {claim}\n\nSources:\n{numbered}"},
    ]


def _parse(text: str) -> Tuple[Dict[int, str], Optional[float]]:
    """Extract ({index: stance}, p) from a panelist reply; tolerant of chatter."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}, None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}, None
    if not isinstance(obj, dict):
        return {}, None
    stances: Dict[int, str] = {}
    for item in obj.get("stances", []) or []:
        if isinstance(item, dict) and isinstance(item.get("i"), int):
            stance = str(item.get("stance", "")).lower()
            if stance in _VALID:
                stances[item["i"]] = stance
    p: Optional[float] = None
    raw_p = obj.get("p")
    if isinstance(raw_p, (int, float)) and 0.0 <= float(raw_p) <= 1.0:
        p = float(raw_p)
    return stances, p


async def _ask(name: str, router, messages) -> Tuple[str, Dict[int, str], Optional[float]]:
    # max_tokens is small on purpose: the reply is one compact JSON object, and
    # free tiers meter tokens-per-minute — headroom here is what keeps a
    # 3-provider panel under the caps.
    result = await router.chat_completion(
        messages, task_type="classification", temperature=0.0, max_tokens=400)
    stances, p = _parse(result.get("text", ""))
    return name, stances, p


def _majority(votes: List[str]) -> Optional[str]:
    """Majority stance; ties (including supports-vs-refutes splits) are neutral —
    a panel that can't agree must not manufacture a direction."""
    if not votes:
        return None
    counts = {s: votes.count(s) for s in _VALID}
    best = max(counts.values())
    winners = [s for s, c in counts.items() if c == best]
    return winners[0] if len(winners) == 1 else "neutral"


def combine_scores(scores: Dict[str, float], weights: Dict[str, float]) -> Optional[float]:
    """Weighted mean of panelist probabilities; uniform when no weights apply."""
    if not scores:
        return None
    applicable = {k: weights.get(k, 0.0) for k in scores}
    total = sum(applicable.values())
    if total <= 0:
        return sum(scores.values()) / len(scores)
    return sum(scores[k] * applicable[k] for k in scores) / total


async def classify_panel(
    claim: str, sources: List[Source], weights: Optional[Dict[str, float]] = None,
) -> Tuple[str, Optional[float], Dict[str, float]]:
    """Mutate `sources` in place with majority stances.

    Returns (classifier_label, ensemble_score, per_panelist_scores).
    """
    evidence = [s for s in sources if not s.stub]
    if not evidence:
        return "none", None, {}

    panel = _build_panel()
    if panel is None:
        return "unavailable", None, {}
    if not panel:
        return "no-providers", None, {}

    messages = _prompt(claim, evidence)
    replies = await asyncio.gather(
        *(_ask(name, router, messages) for name, router in panel),
        return_exceptions=True,
    )

    per_source_votes: Dict[int, List[str]] = {}
    panelist_scores: Dict[str, float] = {}
    answered: List[str] = []
    for reply in replies:
        if isinstance(reply, BaseException):
            continue
        name, stances, p = reply
        if not stances and p is None:
            continue
        answered.append(name)
        for i, stance in stances.items():
            per_source_votes.setdefault(i, []).append(stance)
        if p is not None:
            panelist_scores[name] = p

    if not answered:
        return "error", None, {}

    for i, source in enumerate(evidence):
        stance = _majority(per_source_votes.get(i, []))
        if stance is not None:
            source.stance = stance

    score = combine_scores(panelist_scores, weights or {})
    label = ("ensemble:" + "+".join(sorted(answered))) if len(answered) > 1 \
        else f"free-llm-router:{answered[0]}"
    return label, score, panelist_scores
