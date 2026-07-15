from groundcheck_engine import ensemble


# ── reply parsing ───────────────────────────────────────────────────────────────

def test_parse_clean_reply():
    stances, p = ensemble._parse(
        '{"stances":[{"i":0,"stance":"supports"},{"i":1,"stance":"neutral"}],"p":0.85}')
    assert stances == {0: "supports", 1: "neutral"}
    assert p == 0.85


def test_parse_tolerates_chatter_around_json():
    stances, p = ensemble._parse(
        'Sure! Here is my judgment:\n{"stances":[{"i":0,"stance":"refutes"}],"p":0.1}\nDone.')
    assert stances == {0: "refutes"}
    assert p == 0.1


def test_parse_rejects_bad_stance_and_out_of_range_p():
    stances, p = ensemble._parse('{"stances":[{"i":0,"stance":"maybe"}],"p":1.7}')
    assert stances == {}
    assert p is None


def test_parse_garbage_is_empty():
    assert ensemble._parse("no json here") == ({}, None)
    assert ensemble._parse('{"stances": "nope"}') == ({}, None)


# ── majority vote ───────────────────────────────────────────────────────────────

def test_majority_simple():
    assert ensemble._majority(["supports", "supports", "neutral"]) == "supports"


def test_majority_tie_is_neutral():
    # A supports-vs-refutes split must not manufacture a direction.
    assert ensemble._majority(["supports", "refutes"]) == "neutral"


def test_majority_empty_is_none():
    assert ensemble._majority([]) is None


# ── score combination ───────────────────────────────────────────────────────────

def test_combine_uniform_when_no_weights():
    assert ensemble.combine_scores({"a": 0.8, "b": 0.6}, {}) == 0.7


def test_combine_weighted():
    s = ensemble.combine_scores({"a": 1.0, "b": 0.0}, {"a": 3.0, "b": 1.0})
    assert s == 0.75


def test_combine_ignores_weights_for_absent_panelists():
    # Weight mass on a panelist that did not answer must not distort the mean.
    s = ensemble.combine_scores({"a": 0.8}, {"a": 1.0, "ghost": 9.0})
    assert s == 0.8


def test_combine_empty_is_none():
    assert ensemble.combine_scores({}, {"a": 1.0}) is None


def test_combine_falls_back_to_uniform_when_weights_all_zero():
    import pytest
    s = ensemble.combine_scores({"a": 0.2, "b": 0.4}, {"a": 0.0, "b": 0.0})
    assert s == pytest.approx(0.3)
