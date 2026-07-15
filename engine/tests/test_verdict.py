from groundcheck_engine.models import Source
from groundcheck_engine.verdict import compute_verdict


def src(stance, stub=False):
    return Source(title="", url="", snippet="", stance=stance, stub=stub)


def test_no_sources_unverified_zero():
    v = compute_verdict("x", [])
    assert v["verdict"] == "unverified"
    assert v["confidence"] == 0


def test_stub_only_is_not_evidence():
    v = compute_verdict("x", [src(None, stub=True), src(None, stub=True)])
    assert v["verdict"] == "unverified"
    assert v["confidence"] == 0


def test_no_clear_stance_low_confidence():
    v = compute_verdict("x", [src("neutral"), src(None)])
    assert v["verdict"] == "unverified"
    assert v["confidence"] <= 0.2


def test_two_supports_beats_single_cap():
    v = compute_verdict("x", [src("supports"), src("supports")])
    assert v["verdict"] == "supported"
    assert v["confidence"] > 0.6


def test_lone_support_capped():
    v = compute_verdict("x", [src("supports"), src("neutral")])
    assert v["verdict"] == "supported"
    assert v["confidence"] <= 0.6


def test_conflict_refused_not_majority_voted():
    v = compute_verdict("x", [src("supports"), src("supports"), src("refutes")])
    assert v["verdict"] == "unverified"
    assert "disagree" in v["rationale"]


def test_sufficiency_distinguishes_the_unverified_reasons():
    # the three "unverified" buckets must be tellable apart (SURE-RAG)
    assert compute_verdict("x", [])["sufficiency"] == "no_sources"
    assert compute_verdict("x", [src("neutral"), src("neutral")])["sufficiency"] == "no_stance"
    assert compute_verdict("x", [src("supports"), src("refutes")])["sufficiency"] == "conflict"


def test_lone_source_is_insufficient_but_two_are_sufficient():
    assert compute_verdict("x", [src("supports")])["sufficiency"] == "insufficient"
    assert compute_verdict("x", [src("supports"), src("supports")])["sufficiency"] == "sufficient"


def test_refutes_only():
    v = compute_verdict("x", [src("refutes"), src("refutes")])
    assert v["verdict"] == "refuted"
    assert v["confidence"] > 0.6


def test_confidence_saturates():
    two = compute_verdict("x", [src("supports"), src("supports")])["confidence"]
    three = compute_verdict("x", [src("supports"), src("supports"), src("supports")])["confidence"]
    assert two < three < 1
