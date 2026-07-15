from groundcheck_engine import atoms


# ── decomposition: split only when it's clean ───────────────────────────────────

def test_splits_subject_sharing_conjunction():
    a = atoms.decompose("Marie Curie won two Nobel Prizes and was born in Paris")
    assert len(a) == 2
    # the subject is propagated to the subject-dropping second atom
    assert a[0].startswith("Marie Curie won")
    assert "Marie Curie" in a[1] and "born in Paris" in a[1]


def test_splits_on_semicolon():
    a = atoms.decompose("Water contains hydrogen; the Nile flows through Egypt")
    assert len(a) == 2


def test_atomic_claim_is_left_whole():
    a = atoms.decompose("Paris is the capital of France")
    assert a == ["Paris is the capital of France"]


def test_noun_phrase_conjunction_is_not_split():
    """'black and white' must not be torn into two non-claims — the fragments
    have no predicate, so the whole claim is kept."""
    a = atoms.decompose("The flag is black and white")
    assert a == ["The flag is black and white"]


def test_short_fragment_blocks_the_split():
    a = atoms.decompose("It is red and blue")  # fragments too short / no predicate
    assert len(a) == 1


def test_runaway_split_is_refused():
    long = " and ".join(f"thing number {i} is real" for i in range(8))
    assert atoms.decompose(long) == [long]  # > _MAX_ATOMS -> keep whole


def test_decompose_never_empty():
    assert atoms.decompose("") == [""]
    assert atoms.decompose("and and and") == ["and and and"]


# ── aggregation: weakest link ───────────────────────────────────────────────────

def _v(verdict, conf, suff="sufficient"):
    return {"verdict": verdict, "confidence": conf, "sufficiency": suff}


def test_one_refuted_atom_refutes_the_whole():
    r = atoms.aggregate([_v("supported", 0.9), _v("refuted", 0.8)])
    assert r["verdict"] == "refuted"
    # a false part is reported at the refuting atom's confidence
    assert r["confidence"] == 0.8


def test_all_supported_takes_the_weakest_confidence():
    r = atoms.aggregate([_v("supported", 0.91), _v("supported", 0.55)])
    assert r["verdict"] == "supported"
    assert r["confidence"] == 0.55  # only as strong as the weakest part


def test_one_unverified_atom_blocks_supported():
    r = atoms.aggregate([_v("supported", 0.9),
                         _v("unverified", 0.15, "no_stance")])
    assert r["verdict"] == "unverified"
    assert r["sufficiency"] == "no_stance"


def test_refuted_beats_unverified():
    """A definitely-false part outranks a merely-unproven one."""
    r = atoms.aggregate([_v("refuted", 0.8),
                         _v("unverified", 0.1, "no_sources")])
    assert r["verdict"] == "refuted"


def test_single_atom_passes_through():
    v = _v("supported", 0.8)
    assert atoms.aggregate([v]) == v


def test_empty_is_no_sources():
    r = atoms.aggregate([])
    assert r["verdict"] == "unverified" and r["sufficiency"] == "no_sources"
