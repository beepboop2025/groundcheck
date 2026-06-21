"""Tests for the pydantic contracts — defaults and validation that the rest of
the engine relies on."""
import pytest
from pydantic import ValidationError

from groundcheck_engine.models import ClaimReport, Source, VerifyResult


def test_source_defaults_stance_none_and_not_stub():
    s = Source(title="t", url="u", snippet="sn")
    assert s.stance is None
    assert s.stub is False


def test_source_accepts_valid_stances():
    for st in ("supports", "refutes", "neutral"):
        assert Source(title="t", url="u", snippet="s", stance=st).stance == st


def test_source_rejects_invalid_stance():
    with pytest.raises(ValidationError):
        Source(title="t", url="u", snippet="s", stance="maybe")


def test_verify_result_rejects_invalid_verdict():
    with pytest.raises(ValidationError):
        VerifyResult(
            claim="c", verdict="probably", confidence=0.5, rationale="r",
            backend="wikipedia", classifier="none", sources=[],
        )


def test_verify_result_roundtrips_nested_sources():
    vr = VerifyResult(
        claim="c", verdict="supported", confidence=0.8, rationale="r",
        backend="wikipedia", classifier="free-llm-router",
        sources=[Source(title="t", url="u", snippet="s", stance="supports")],
    )
    dumped = vr.model_dump()
    assert dumped["sources"][0]["stance"] == "supports"
    assert dumped["verdict"] == "supported"


def test_claim_report_requires_all_fields():
    with pytest.raises(ValidationError):
        ClaimReport(claim="c", verdict="supported")  # missing confidence/rationale
