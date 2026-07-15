"""Evidence-bound provenance: the receipt must prove HOW the verdict was
reached. The tests that matter are the tamper tests — swapping a source's
content, flipping a stance, reordering evidence, or changing the route must
break verification; benign re-serialization must not."""
import copy

from groundcheck_engine import attest, provenance


def _resp(sources, classifier="ensemble:groq+cerebras", atoms=None):
    return {
        "claim": "Paris is the capital of France.",
        "verdict": "supported", "confidence": 0.9,
        "backend": "wikipedia+gdelt", "classifier": classifier,
        "sources": sources, "atoms": atoms,
    }


SRC = [
    {"url": "https://en.wikipedia.org/wiki/Paris", "snippet": "Paris is the capital of France.",
     "stance": "supports", "stub": False},
    {"url": "https://news.example/x", "snippet": "France's capital city is Paris.",
     "stance": "supports", "stub": False},
]


# ── evidence chain determinism + structure ──────────────────────────────────────

def test_evidence_chain_is_deterministic():
    a = provenance.evidence_chain(SRC)
    b = provenance.evidence_chain(copy.deepcopy(SRC))
    assert a["root"] == b["root"]
    assert a["n_items"] == 2


def test_stub_sources_excluded_from_chain():
    with_stub = SRC + [{"url": "https://stub", "snippet": "", "stance": None, "stub": True}]
    assert provenance.evidence_chain(with_stub)["root"] == provenance.evidence_chain(SRC)["root"]
    assert provenance.evidence_chain(with_stub)["n_items"] == 2


# ── tamper detection: the whole point ───────────────────────────────────────────

def test_changing_a_snippet_changes_the_root():
    tampered = copy.deepcopy(SRC)
    tampered[0]["snippet"] = "Paris is NOT the capital of France."
    assert provenance.evidence_chain(tampered)["root"] != provenance.evidence_chain(SRC)["root"]


def test_flipping_a_stance_changes_the_root():
    tampered = copy.deepcopy(SRC)
    tampered[0]["stance"] = "refutes"
    assert provenance.evidence_chain(tampered)["root"] != provenance.evidence_chain(SRC)["root"]


def test_reordering_evidence_changes_the_root():
    assert provenance.evidence_chain(SRC[::-1])["root"] != provenance.evidence_chain(SRC)["root"]


# ── route binding ───────────────────────────────────────────────────────────────

def test_route_hash_binds_the_model():
    single = provenance.route_hash(_resp(SRC, classifier="free-llm-router:groq"))
    panel = provenance.route_hash(_resp(SRC, classifier="ensemble:groq+cerebras"))
    assert single != panel  # a silent model swap changes the route hash


def test_route_hash_binds_decomposition():
    flat = provenance.route_hash(_resp(SRC))
    decomposed = provenance.route_hash(_resp(SRC, atoms=[{"claim": "x"}]))
    assert flat != decomposed


# ── attestation mode is honest ──────────────────────────────────────────────────

def test_mode_is_software_without_a_quote(monkeypatch):
    monkeypatch.delenv("GROUNDCHECK_TEE_QUOTE", raising=False)
    assert provenance.attestation_mode() == "software"
    p = provenance.build_provenance(_resp(SRC))
    assert p["attestation_mode"] == "software" and "tee_quote" not in p


def test_mode_is_tee_only_with_a_quote(monkeypatch):
    monkeypatch.setenv("GROUNDCHECK_TEE_QUOTE", "deadbeef-quote")
    p = provenance.build_provenance(_resp(SRC))
    assert p["attestation_mode"] == "tee" and p["tee_quote"] == "deadbeef-quote"


# ── end-to-end: the signature binds the evidence path ───────────────────────────

def test_signed_receipt_covers_the_evidence_path():
    resp = _resp(SRC)
    resp["provenance"] = provenance.build_provenance(resp)
    resp["attestation"] = attest.attest_verify_response(resp)
    # a valid response verifies
    assert attest.verify_attested_response("verify", resp)["valid"]


def test_tampering_evidence_breaks_the_receipt():
    resp = _resp(SRC)
    resp["provenance"] = provenance.build_provenance(resp)
    resp["attestation"] = attest.attest_verify_response(resp)
    # a holder flips a source's snippet AFTER signing — verification must fail,
    # because evidence_root is bound into the signed manifest
    resp["sources"][0]["snippet"] = "tampered evidence text"
    result = attest.verify_attested_response("verify", resp)
    assert not result["valid"]


def test_tampering_route_breaks_the_receipt():
    resp = _resp(SRC)
    resp["provenance"] = provenance.build_provenance(resp)
    resp["attestation"] = attest.attest_verify_response(resp)
    resp["classifier"] = "free-llm-router:some-other-model"  # pretend a cheaper model answered
    assert not attest.verify_attested_response("verify", resp)["valid"]


def test_benign_reserialization_still_verifies():
    resp = _resp(SRC)
    resp["provenance"] = provenance.build_provenance(resp)
    resp["attestation"] = attest.attest_verify_response(resp)
    roundtripped = copy.deepcopy(resp)  # order/whitespace-insensitive manifest
    assert attest.verify_attested_response("verify", roundtripped)["valid"]
