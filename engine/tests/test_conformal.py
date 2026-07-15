import json

import pytest

from groundcheck_engine import conformal


@pytest.fixture(autouse=True)
def _fresh_cache():
    conformal.reset_cache()
    yield
    conformal.reset_cache()


# ── finite-sample quantiles ─────────────────────────────────────────────────────

def test_upper_threshold_exact_order_statistic():
    # n=19, alpha=0.1 -> k = ceil(20*0.9) = 18 -> 18th smallest of the false scores.
    scores = [i / 20 for i in range(1, 20)]  # 0.05 .. 0.95
    assert conformal.upper_threshold(scores, 0.1) == 18 / 20


def test_upper_threshold_refuses_small_n():
    # n=5, alpha=0.1 -> k = ceil(6*0.9) = 6 > 5 -> cannot certify at this alpha.
    assert conformal.upper_threshold([0.1, 0.2, 0.3, 0.4, 0.5], 0.1) is None
    assert conformal.upper_threshold([], 0.1) is None


def test_lower_threshold_exact_order_statistic():
    # n=19, alpha=0.1 -> k = floor(20*0.1) = 2 -> 2nd smallest of the true scores.
    scores = [i / 20 for i in range(1, 20)]
    assert conformal.lower_threshold(scores, 0.1) == 2 / 20


def test_lower_threshold_refuses_small_n():
    # n=5, alpha=0.1 -> k = floor(6*0.1) = 0 -> cannot certify.
    assert conformal.lower_threshold([0.1, 0.2, 0.3, 0.4, 0.5], 0.1) is None


def test_guarantee_holds_empirically():
    """The whole point: with the +1 correction, at most ~alpha of fresh false
    claims score above the supported threshold, for ANY score distribution."""
    import random
    rng = random.Random(7)
    alpha = 0.1
    violations = 0
    trials = 400
    for _ in range(trials):
        false_scores = [rng.betavariate(2, 5) for _ in range(40)]
        t = conformal.upper_threshold(false_scores, alpha)
        fresh = rng.betavariate(2, 5)
        if t is not None and fresh > t:
            violations += 1
    assert violations / trials <= alpha + 0.04  # sampling slack


# ── artifact + certification ────────────────────────────────────────────────────

def _write_artifact(tmp_path, monkeypatch, groups):
    art = conformal.build_artifact(
        alpha=0.1, created_at="2026-07-15T00:00:00+00:00",
        scores_by_group=groups, weights={"groq": 0.5, "cerebras": 0.5})
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(art))
    monkeypatch.setattr(conformal.config, "CALIBRATION_PATH", str(path))
    conformal.reset_cache()
    return art


GROUPS = {
    "general": {"true": [0.7 + i / 100 for i in range(20)],
                "false": [0.1 + i / 100 for i in range(20)]},
    "global": {"true": [0.7 + i / 100 for i in range(20)],
               "false": [0.1 + i / 100 for i in range(20)]},
}


def test_no_artifact_means_no_guarantee(monkeypatch):
    monkeypatch.setattr(conformal.config, "CALIBRATION_PATH", "/nonexistent/x.json")
    conformal.reset_cache()
    assert conformal.certify("supported", 0.99, "general") is None


def test_certified_supported_above_threshold(tmp_path, monkeypatch):
    _write_artifact(tmp_path, monkeypatch, GROUPS)
    g = conformal.certify("supported", 0.99, "general")
    assert g["certified"] is True
    assert g["alpha"] == 0.1
    assert g["group"] == "general"


def test_uncertified_below_threshold(tmp_path, monkeypatch):
    _write_artifact(tmp_path, monkeypatch, GROUPS)
    g = conformal.certify("supported", 0.2, "general")
    assert g["certified"] is False


def test_refuted_uses_lower_tail(tmp_path, monkeypatch):
    _write_artifact(tmp_path, monkeypatch, GROUPS)
    assert conformal.certify("refuted", 0.01, "general")["certified"] is True
    assert conformal.certify("refuted", 0.9, "general")["certified"] is False


def test_unknown_group_falls_back_to_global(tmp_path, monkeypatch):
    _write_artifact(tmp_path, monkeypatch, GROUPS)
    g = conformal.certify("supported", 0.99, "exotic-domain")
    assert g["group"] == "global"
    assert g["certified"] is True


def test_missing_score_never_certifies(tmp_path, monkeypatch):
    _write_artifact(tmp_path, monkeypatch, GROUPS)
    g = conformal.certify("supported", None, "general")
    assert g["certified"] is False


def test_corrupt_artifact_degrades_to_none(tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    path.write_text("{not json")
    monkeypatch.setattr(conformal.config, "CALIBRATION_PATH", str(path))
    conformal.reset_cache()
    assert conformal.certify("supported", 0.99, "general") is None


def test_wrong_version_degrades_to_none(tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps({"version": 99, "alpha": 0.1, "groups": {}}))
    monkeypatch.setattr(conformal.config, "CALIBRATION_PATH", str(path))
    conformal.reset_cache()
    assert conformal.certify("supported", 0.99, "general") is None
