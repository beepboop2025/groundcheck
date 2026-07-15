"""Typed contracts shared across the engine (and mirrored by the TS server's types.ts)."""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

Stance = Literal["supports", "refutes", "neutral"]
Verdict = Literal["supported", "refuted", "unverified"]
# Why a verdict is (not) directional — SURE-RAG three-way distinction.
Sufficiency = Literal["sufficient", "insufficient", "no_sources", "no_stance", "conflict"]


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


class AtomReport(BaseModel):
    """One atomic sub-claim of a compound claim, verified on its own evidence."""
    claim: str
    verdict: Verdict
    confidence: float
    sufficiency: Optional[Sufficiency] = None


class VerifyResult(BaseModel):
    claim: str
    verdict: Verdict
    confidence: float
    rationale: str
    backend: str
    classifier: str
    sources: List[Source]
    instruments: List[ClaimInstrument] = []
    # Why the verdict is (not) directional — distinguishes no-evidence from
    # conflict from a real-but-insufficient lean (SURE-RAG three-way).
    sufficiency: Optional[Sufficiency] = None
    # Present when the claim was compound and split into atoms (atoms.py); the
    # compound verdict is the weakest-link aggregate of these.
    atoms: Optional[List[AtomReport]] = None
    # Weighted multi-model panel probability that the claim is true (ensemble.py).
    ensemble_score: Optional[float] = None
    # Conformal guarantee; absent on uncalibrated deployments.
    guarantee: Optional[Guarantee] = None
    # Evidence-bound provenance: rolling commitment over the evidence path +
    # model route, bound into the signed manifest (provenance.py).
    provenance: Optional[Dict[str, Any]] = None
    # Signed receipt over a deterministic subset of this response (attest.py).
    attestation: Optional[Dict[str, Any]] = None


class ClaimReport(BaseModel):
    claim: str
    verdict: Verdict
    confidence: float
    rationale: str
    sufficiency: Optional[Sufficiency] = None
    guarantee: Optional[Guarantee] = None


class CheckResult(BaseModel):
    checked: int
    backend: str
    classifier: str
    report: List[ClaimReport]
    # Signed receipt over a deterministic subset of this response (attest.py).
    attestation: Optional[Dict[str, Any]] = None


# How a paid delivery compares to what was advertised (delivery.py):
# consistency, never merit — "as advertised", not "good service".
DeliveryVerdict = Literal["consistent", "degraded", "inconsistent", "unverifiable"]


class DeliveryGrounding(BaseModel):
    """Grounding tally over the factual claims in a delivered response."""
    checked: int
    supported: int
    refuted: int
    unverified: int
    mean_confidence: Optional[float] = None
    report: List[ClaimReport]


class DeliveryConformance(BaseModel):
    """Structural check of the delivered payload against the schema the
    service advertised (402 offer / Bazaar listing). checked=False when the
    buyer supplied no schema."""
    checked: bool
    valid: Optional[bool] = None
    problems: List[str] = []


class DeliveryPayment(BaseModel):
    """The x402 settlement receipt the buyer presented, bound by hash into
    the attestation. Binding records WHAT was presented; confirming the
    transaction on-chain is the verifier's own step."""
    bound: bool
    receipt_sha256: Optional[str] = None
    network: Optional[str] = None
    transaction: Optional[str] = None
    payer: Optional[str] = None
    success: Optional[bool] = None
    problems: List[str] = []


class DeliveryResult(BaseModel):
    """POST /attest-delivery: a signed, offline-verifiable receipt binding an
    agent's payment to what the paid service actually delivered."""
    service: str
    delivery_verdict: DeliveryVerdict
    rationale: str
    response_sha256: str
    request_sha256: Optional[str] = None
    grounding: DeliveryGrounding
    conformance: DeliveryConformance
    payment: DeliveryPayment
    backend: str
    classifier: str
    # Signed receipt (kind "delivery") over a deterministic subset (attest.py).
    attestation: Optional[Dict[str, Any]] = None


class ExtractResult(BaseModel):
    """POST /extract: checkable atomic claims pulled from text — the cheap
    first step of a verification loop (extract -> ground -> attest)."""
    count: int
    claims: List[str]
    method: str
    input_sha256: str
    # Signed receipt (kind "extract") bound to the input hash (attest.py).
    attestation: Optional[Dict[str, Any]] = None
