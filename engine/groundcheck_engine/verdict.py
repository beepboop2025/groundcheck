"""compute_verdict — the brain of Groundcheck (ported from the original verdict.js).

Design decisions, and how they're resolved here:
  1. NON-EVIDENCE pulls toward unverified. Stub sources are dropped; null/neutral stances
     never create a verdict. An unconfigured pipeline can NEVER look "supported".
  2. CONFLICT is refused, not majority-voted. Any support + any refute -> "unverified".
  3. ONE source is a lean, not a ruling: a lone source caps confidence at SINGLE_CAP.
  4. CONFIDENCE SATURATES: 1 - DECAY**n, so each extra agreeing source adds less.

Tune the three constants to move the cautious/decisive trade-off.
"""
from typing import Dict, List

from .models import Source

SINGLE_CAP = 0.6      # max confidence from a lone supporting/refuting source
DECAY = 0.45          # smaller -> confidence rises faster with more agreeing sources
CONFLICT_CONF = 0.25  # confidence reported when sources disagree


def _saturate(n: int) -> float:
    # 1 -> 0.55, 2 -> 0.80, 3 -> 0.91 (DECAY=0.45). Never reaches 1.
    return 1 - DECAY ** n


def compute_verdict(claim: str, sources: List[Source]) -> Dict[str, object]:
    evidence = [s for s in sources if not s.stub]
    if not evidence:
        return {"verdict": "unverified", "confidence": 0.0, "rationale": "No live sources."}

    supports = sum(1 for s in evidence if s.stance == "supports")
    refutes = sum(1 for s in evidence if s.stance == "refutes")

    # (2) Conflict -> refuse to call it.
    if supports > 0 and refutes > 0:
        return {
            "verdict": "unverified",
            "confidence": CONFLICT_CONF,
            "rationale": f"Sources disagree ({supports} support, {refutes} refute).",
        }

    # (1) Only neutral/unknown stances -> evidence exists but says nothing decisive.
    if supports == 0 and refutes == 0:
        return {
            "verdict": "unverified",
            "confidence": 0.15,
            "rationale": f"{len(evidence)} source(s) found, none took a clear stance.",
        }

    verdict, n = ("supported", supports) if supports > 0 else ("refuted", refutes)
    # (3)(4) Cap a lone source; saturate beyond.
    confidence = min(SINGLE_CAP, _saturate(1)) if n == 1 else _saturate(n)
    verb = "support" if verdict == "supported" else "refute"
    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "rationale": f"{n} source(s) {verb} the claim, none disagree.",
    }
