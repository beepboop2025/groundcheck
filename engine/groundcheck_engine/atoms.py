"""Atomic-claim decomposition — split a compound claim into independently
checkable facts before verifying (Fact in Fragments, arXiv:2506.07446).

WHY. A single retrieve->verdict pass over "Marie Curie won two Nobel Prizes and
was born in Paris" checks ONE blended claim: the true half ("won two Nobel
Prizes") can carry the false half ("born in Paris" — she was born in Warsaw)
past a support verdict, because the supporting sources are real. Decomposition
verifies each atom on its own evidence and recombines with a WEAKEST-LINK rule,
so one false conjunct refutes the whole and one unproven conjunct blocks a
"supported".

HOW (rule-based, no LLM — the split must be as auditable as the verdict). We
split only on high-precision, surface conjunction boundaries: "; ", " and ",
" as well as ", "; and ". We deliberately DO NOT attempt clause-level semantic
decomposition (that needs a model and would make the front-end unauditable);
the cost of under-splitting is falling back to single-claim behavior, which is
the current, safe default. Splits that produce a fragment too short to be a
standalone factual claim are rejected and the claim is left whole.

Aggregation rule over atom verdicts (a, b, ...):
    any refuted            -> refuted     (a false part makes the whole false)
    all supported          -> supported
    otherwise              -> unverified, with the weakest atom's sufficiency
"""
from __future__ import annotations

import re
from typing import Dict, List

# Conjunction boundaries, longest first so " as well as " wins over " as ".
_SPLIT = re.compile(
    r"\s*;\s*and\s+|\s*;\s+|\s+as\s+well\s+as\s+|\s+and\s+also\s+|\s+and\s+",
    re.IGNORECASE,
)
_MIN_ATOM_CHARS = 12          # below this, a fragment is not a standalone claim
_MAX_ATOMS = 6                # a runaway split is a sign we misread the sentence
# A standalone factual atom needs its own predicate; a bare noun phrase does not.
_HAS_PREDICATE = re.compile(
    r"\b(is|are|was|were|has|have|had|won|born|located|founded|wrote|discovered|"
    r"invented|developed|equals?|contains?|orbits?|flows?|measures?|costs?|"
    r"trades?|owns?|holds?|includes?)\b",
    re.IGNORECASE,
)


def _carries_subject(atom: str) -> bool:
    """A trailing conjunct often drops the subject ('... and was born in Paris').
    Such an atom is still checkable IF it keeps a predicate; a fragment that is
    only a predicate tail with no content word is not."""
    return bool(_HAS_PREDICATE.search(atom)) and len(atom.split()) >= 3


def decompose(claim: str) -> List[str]:
    """Return the atomic sub-claims of `claim`, or [claim] when it is already
    atomic or cannot be split cleanly. Never returns an empty list."""
    parts = [p.strip(" ,.;") for p in _SPLIT.split(claim) if p.strip(" ,.;")]
    if len(parts) < 2 or len(parts) > _MAX_ATOMS:
        return [claim]
    # Every part must look like a standalone factual claim; if any doesn't, the
    # split was probably syntactic noise ("black and white") — keep the whole.
    atoms = []
    for p in parts:
        if len(p) < _MIN_ATOM_CHARS or not _carries_subject(p):
            return [claim]
        atoms.append(p)
    # Propagate the leading subject to subject-dropping later atoms so each atom
    # retrieves on its own terms: "Marie Curie won ... and was born in Paris"
    # -> ["Marie Curie won ...", "Marie Curie was born in Paris"].
    subject = _leading_subject(atoms[0])
    if subject:
        atoms = [atoms[0]] + [_prefix_subject(a, subject) for a in atoms[1:]]
    return atoms


def _leading_subject(atom: str) -> str:
    """Best-effort subject: the words before the first predicate verb. Empty if
    we can't find one confidently (then no subject is propagated)."""
    m = _HAS_PREDICATE.search(atom)
    if not m or m.start() == 0:
        return ""
    subject = atom[: m.start()].strip()
    return subject if 0 < len(subject.split()) <= 5 else ""


def _prefix_subject(atom: str, subject: str) -> str:
    """Prepend the subject only when the atom appears to have dropped it (starts
    with a predicate verb). An atom that already names a subject is left alone."""
    first = atom.split(" ", 1)[0].lower()
    if _HAS_PREDICATE.match(atom) or first in ("was", "were", "is", "are", "had", "has"):
        return f"{subject} {atom}"
    return atom


def aggregate(atom_verdicts: List[Dict[str, object]]) -> Dict[str, object]:
    """Weakest-link recombination of per-atom verdict dicts.

    Each input dict has at least {verdict, confidence, sufficiency}. Returns the
    combined verdict for the compound claim.
    """
    if not atom_verdicts:
        return {"verdict": "unverified", "confidence": 0.0, "sufficiency": "no_sources",
                "rationale": "No atoms to verify."}
    if len(atom_verdicts) == 1:
        return atom_verdicts[0]

    verdicts = [v["verdict"] for v in atom_verdicts]
    refuted = [v for v in atom_verdicts if v["verdict"] == "refuted"]
    if refuted:
        weakest = min(refuted, key=lambda v: v["confidence"])
        return {
            "verdict": "refuted",
            "confidence": weakest["confidence"],
            "sufficiency": weakest.get("sufficiency", "sufficient"),
            "rationale": f"{len(refuted)} of {len(atom_verdicts)} parts are refuted "
                         f"— a false part makes the whole claim false.",
        }
    if all(v == "supported" for v in verdicts):
        # The compound is only as strong as its weakest supported atom.
        weakest = min(atom_verdicts, key=lambda v: v["confidence"])
        return {
            "verdict": "supported",
            "confidence": round(weakest["confidence"], 2),
            "sufficiency": weakest.get("sufficiency", "sufficient"),
            "rationale": f"All {len(atom_verdicts)} parts are independently supported "
                         f"(confidence set by the weakest part).",
        }
    # Some part is unverified: the compound cannot be certified.
    unproven = [v for v in atom_verdicts if v["verdict"] == "unverified"]
    weakest = min(unproven, key=lambda v: v["confidence"])
    return {
        "verdict": "unverified",
        "confidence": weakest["confidence"],
        "sufficiency": weakest.get("sufficiency", "no_stance"),
        "rationale": f"{len(unproven)} of {len(atom_verdicts)} parts could not be "
                     f"verified — the whole claim is not established.",
    }
