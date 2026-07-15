"""Typed contracts shared across the engine (and mirrored by the TS server's types.ts)."""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

Stance = Literal["supports", "refutes", "neutral"]
Verdict = Literal["supported", "refuted", "unverified"]


class Source(BaseModel):
    title: str
    url: str
    snippet: str
    stance: Optional[Stance] = None
    stub: bool = False


class Instrument(BaseModel):
    """One canonical security record from open symbology (OpenFIGI)."""
    figi: str
    name: Optional[str] = None
    ticker: Optional[str] = None
    exch_code: Optional[str] = None
    security_type: Optional[str] = None
    market_sector: Optional[str] = None
    composite_figi: Optional[str] = None
    share_class_figi: Optional[str] = None
    description: Optional[str] = None


class InstrumentProvenance(BaseModel):
    source: str
    url: str
    retrieved_at: str
    authenticated: bool = False


class ResolveResult(BaseModel):
    query: str
    id_type: Optional[str] = None
    matched: bool
    instruments: List[Instrument]
    provenance: InstrumentProvenance
    note: Optional[str] = None


class ClaimInstrument(BaseModel):
    """An explicit security reference found in a claim, resolved (or not)."""
    reference: str
    id_type: str
    resolved: bool
    instrument: Optional[Instrument] = None


class Guarantee(BaseModel):
    """Conformal certification of a verdict (conformal.py). Present only when a
    calibration artifact is deployed; certified=True means the ensemble score
    cleared a finite-sample threshold giving error probability <= alpha."""
    certified: bool
    alpha: float
    group: str
    score: Optional[float] = None
    threshold: Optional[float] = None
    n_calibration: Optional[int] = None
    calibrated_at: Optional[str] = None


class VerifyResult(BaseModel):
    claim: str
    verdict: Verdict
    confidence: float
    rationale: str
    backend: str
    classifier: str
    sources: List[Source]
    instruments: List[ClaimInstrument] = []
    # Weighted multi-model panel probability that the claim is true (ensemble.py).
    ensemble_score: Optional[float] = None
    # Conformal guarantee; absent on uncalibrated deployments.
    guarantee: Optional[Guarantee] = None
    # Signed receipt over a deterministic subset of this response (attest.py).
    attestation: Optional[Dict[str, Any]] = None


class ClaimReport(BaseModel):
    claim: str
    verdict: Verdict
    confidence: float
    rationale: str
    guarantee: Optional[Guarantee] = None


class CheckResult(BaseModel):
    checked: int
    backend: str
    classifier: str
    report: List[ClaimReport]
    # Signed receipt over a deterministic subset of this response (attest.py).
    attestation: Optional[Dict[str, Any]] = None
