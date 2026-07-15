"""Split-conformal certification of verdicts — the "≤ alpha error" guarantee.

The idea (Multi-LLM Adaptive Conformal Inference, arXiv:2602.01285, adapted):
instead of trusting a hand-tuned confidence formula, we calibrate thresholds on
a labeled claim set and certify a verdict only when the ensemble score clears a
finite-sample quantile. The guarantees are distribution-free and exact:

  supported: certified only if score > the ceil((n+1)(1-alpha))-th order statistic
             of the scores that FALSE calibration claims achieved. Under
             exchangeability, P(a false claim gets certified "supported") <= alpha.
  refuted:   symmetric against the lower tail of TRUE claims' scores, so
             P(a true claim gets certified "refuted") <= alpha.

Mondrian grouping: thresholds are computed per claim-group (e.g. "instrument"
vs "general") with a "global" fallback, because miscoverage hides in subgroups.

Honest degradation, same ethos as the rest of the engine: no calibration
artifact on disk -> no guarantee is ever claimed; the verdict falls back to the
uncalibrated heuristic and says so.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import config

ARTIFACT_VERSION = 1


# ── finite-sample quantiles (the +1 correction is what makes the bound exact) ──

def upper_threshold(false_scores: List[float], alpha: float) -> Optional[float]:
    """Smallest t such that P(false-claim score > t) <= alpha, finite-sample.

    Returns None when the calibration set is too small to certify at this
    alpha (k would exceed n) — refusing beats pretending.
    """
    n = len(false_scores)
    k = math.ceil((n + 1) * (1 - alpha))
    if n == 0 or k > n:
        return None
    return sorted(false_scores)[k - 1]


def lower_threshold(true_scores: List[float], alpha: float) -> Optional[float]:
    """Largest t such that P(true-claim score < t) <= alpha, finite-sample."""
    n = len(true_scores)
    k = math.floor((n + 1) * alpha)
    if n == 0 or k < 1:
        return None
    return sorted(true_scores)[k - 1]


# ── calibration artifact ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GroupThresholds:
    supported_threshold: Optional[float]
    refuted_threshold: Optional[float]
    n_false: int
    n_true: int


@dataclass(frozen=True)
class Calibration:
    alpha: float
    created_at: str
    groups: Dict[str, GroupThresholds]
    weights: Dict[str, float]  # provider name -> ensemble weight

    def thresholds_for(self, group: str) -> tuple[str, Optional[GroupThresholds]]:
        """Resolve a claim group to usable thresholds, falling back to global."""
        g = self.groups.get(group)
        if g and (g.supported_threshold is not None or g.refuted_threshold is not None):
            return group, g
        g = self.groups.get("global")
        if g:
            return "global", g
        return group, None


def build_artifact(alpha: float, created_at: str,
                   scores_by_group: Dict[str, Dict[str, List[float]]],
                   weights: Dict[str, float]) -> dict:
    """scores_by_group: group -> {"true": [...], "false": [...]} ensemble scores."""
    groups = {}
    for name, s in scores_by_group.items():
        groups[name] = {
            "supported_threshold": upper_threshold(s.get("false", []), alpha),
            "refuted_threshold": lower_threshold(s.get("true", []), alpha),
            "n_false": len(s.get("false", [])),
            "n_true": len(s.get("true", [])),
        }
    return {
        "version": ARTIFACT_VERSION,
        "alpha": alpha,
        "created_at": created_at,
        "method": "split-conformal, finite-sample order statistics with +1 correction",
        "weights": weights,
        "groups": groups,
    }


_calibration: Optional[Calibration] = None
_calibration_loaded = False


def load_calibration() -> Optional[Calibration]:
    """Load (and cache) the calibration artifact; None when absent/invalid."""
    global _calibration, _calibration_loaded
    if _calibration_loaded:
        return _calibration
    _calibration_loaded = True
    path = config.CALIBRATION_PATH
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if raw.get("version") != ARTIFACT_VERSION:
            return None
        groups = {
            name: GroupThresholds(
                supported_threshold=g.get("supported_threshold"),
                refuted_threshold=g.get("refuted_threshold"),
                n_false=int(g.get("n_false", 0)),
                n_true=int(g.get("n_true", 0)),
            )
            for name, g in raw.get("groups", {}).items()
        }
        _calibration = Calibration(
            alpha=float(raw["alpha"]),
            created_at=str(raw.get("created_at", "")),
            groups=groups,
            weights={k: float(v) for k, v in raw.get("weights", {}).items()},
        )
    except (OSError, ValueError, KeyError, TypeError):
        _calibration = None
    return _calibration


def reset_cache() -> None:
    """Test hook: force the next load_calibration() to re-read disk."""
    global _calibration, _calibration_loaded
    _calibration = None
    _calibration_loaded = False


# ── certification ───────────────────────────────────────────────────────────────

def certify(verdict: str, score: Optional[float], group: str) -> Optional[dict]:
    """Return a guarantee dict for a supported/refuted verdict, or None.

    None means "no calibration deployed" — the caller should stay silent about
    guarantees. A dict with certified=False means calibration exists but this
    verdict did not clear its threshold (or direction can't be certified).
    """
    cal = load_calibration()
    if cal is None:
        return None
    resolved_group, thresholds = cal.thresholds_for(group)
    base = {
        "certified": False,
        "alpha": cal.alpha,
        "group": resolved_group,
        "score": score,
        "calibrated_at": cal.created_at,
    }
    if thresholds is None or score is None:
        return base
    if verdict == "supported" and thresholds.supported_threshold is not None:
        base["threshold"] = thresholds.supported_threshold
        base["n_calibration"] = thresholds.n_false
        base["certified"] = score > thresholds.supported_threshold
    elif verdict == "refuted" and thresholds.refuted_threshold is not None:
        base["threshold"] = thresholds.refuted_threshold
        base["n_calibration"] = thresholds.n_true
        base["certified"] = score < thresholds.refuted_threshold
    return base
