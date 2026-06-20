"""Typed contracts shared across the engine (and mirrored by the TS server's types.ts)."""
from typing import List, Literal, Optional

from pydantic import BaseModel

Stance = Literal["supports", "refutes", "neutral"]
Verdict = Literal["supported", "refuted", "unverified"]


class Source(BaseModel):
    title: str
    url: str
    snippet: str
    stance: Optional[Stance] = None
    stub: bool = False


class VerifyResult(BaseModel):
    claim: str
    verdict: Verdict
    confidence: float
    rationale: str
    backend: str
    classifier: str
    sources: List[Source]


class ClaimReport(BaseModel):
    claim: str
    verdict: Verdict
    confidence: float
    rationale: str


class CheckResult(BaseModel):
    checked: int
    backend: str
    classifier: str
    report: List[ClaimReport]
