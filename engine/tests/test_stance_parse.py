"""Tests for stance._parse — the JSON-extraction step that turns a noisy LLM
reply into a {source_index: stance} map. Pure string/JSON logic, no network."""
from groundcheck_engine.stance import _parse


def test_extracts_clean_array():
    out = _parse('[{"i":0,"stance":"supports"},{"i":1,"stance":"refutes"}]')
    assert out == {0: "supports", 1: "refutes"}


def test_pulls_array_out_of_chatty_wrapper():
    # LLMs love prepending prose / code fences; we grab the first JSON array.
    text = 'Sure! Here is the result:\n```json\n[{"i":0,"stance":"neutral"}]\n```\nHope that helps.'
    assert _parse(text) == {0: "neutral"}


def test_stance_is_lowercased():
    assert _parse('[{"i":0,"stance":"SUPPORTS"}]') == {0: "supports"}


def test_no_array_returns_empty():
    assert _parse("I could not determine a stance.") == {}


def test_malformed_json_returns_empty_not_raises():
    # A bracketed but invalid body must degrade to {}, never throw.
    assert _parse('[{"i":0, stance:supports}]') == {}


def test_non_integer_index_is_skipped():
    # i must be an int; "0" (string) is dropped, valid neighbours survive.
    out = _parse('[{"i":"0","stance":"supports"},{"i":1,"stance":"refutes"}]')
    assert out == {1: "refutes"}


def test_missing_stance_becomes_empty_string():
    # The verdict layer later filters on the _VALID set, so an empty stance
    # here is harmless — but it must still be a string, not raise.
    out = _parse('[{"i":2}]')
    assert out == {2: ""}


def test_greedy_span_across_two_arrays_is_invalid_json():
    # The regex is greedy `\[.*\]`, so with two arrays it captures from the
    # first `[` to the LAST `]` — including the prose between them. That span
    # is not valid JSON, so it degrades to {} rather than guessing.
    out = _parse('noise [{"i":0,"stance":"supports"}] and [{"i":9,"stance":"refutes"}]')
    assert out == {}


def test_non_dict_entries_are_ignored():
    out = _parse('[42, "x", {"i":0,"stance":"supports"}]')
    assert out == {0: "supports"}
