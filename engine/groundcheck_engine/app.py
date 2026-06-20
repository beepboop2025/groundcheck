"""FastAPI app exposing the Groundcheck engine.

Endpoints:
  GET  /            interactive landing page / live demo (HTML)
  GET  /health      liveness + which retrieval backend is active
  GET  /search?q=&n=  the documented GROUNDCHECK_SEARCH_URL contract ({results:[...]})
  POST /verify      retrieve -> classify stance -> verdict for one claim
  POST /check       extract claims from text and verify each
"""
import re
import time
from collections import defaultdict, deque
from typing import Deque, Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .landing import LANDING_HTML
from .models import CheckResult, ClaimReport, Source, VerifyResult
from .retrieval import Retriever
from .stance import classify_stances
from .verdict import compute_verdict

app = FastAPI(title="Groundcheck Engine", version="0.2.0")
retriever = Retriever()

_SENTENCE = re.compile(r"(?<=[.!?])\s+")

# Best-effort per-IP rate limit on the public demo, to protect free LLM quota.
# (Serverless instances are ephemeral, so this caps bursts per warm instance.)
_RL_WINDOW = 60.0
_RL_MAX = 30
_hits: Dict[str, Deque[float]] = defaultdict(deque)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.method == "POST":
        ip = request.client.host if request.client else "anon"
        now = time.time()
        dq = _hits[ip]
        while dq and now - dq[0] > _RL_WINDOW:
            dq.popleft()
        if len(dq) >= _RL_MAX:
            return JSONResponse({"error": "rate limited — try again in a minute"}, status_code=429)
        dq.append(now)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def landing() -> str:
    return LANDING_HTML


class VerifyRequest(BaseModel):
    claim: str
    max_sources: int = Field(5, ge=1, le=10)


class CheckRequest(BaseModel):
    text: str
    max_claims: int = Field(8, ge=1, le=20)


def _extract_claims(text: str, max_claims: int) -> List[str]:
    parts = (p.strip() for p in _SENTENCE.split(text))
    return [p for p in parts if len(p) > 20][:max_claims]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "backend": retriever.backend, "version": app.version}


@app.get("/search")
async def search(q: str, n: int = 5) -> dict:
    sources = await retriever.search(q, n)
    return {"results": [s.model_dump() for s in sources]}


@app.post("/verify", response_model=VerifyResult)
async def verify(req: VerifyRequest) -> VerifyResult:
    sources = await retriever.search(req.claim, req.max_sources)
    classifier = await classify_stances(req.claim, sources)
    v = compute_verdict(req.claim, sources)
    return VerifyResult(
        claim=req.claim, backend=retriever.backend, classifier=classifier, sources=sources, **v
    )


@app.post("/check", response_model=CheckResult)
async def check(req: CheckRequest) -> CheckResult:
    claims = _extract_claims(req.text, req.max_claims)
    report: List[ClaimReport] = []
    classifier = "none"
    for claim in claims:
        sources = await retriever.search(claim, 4)
        classifier = await classify_stances(claim, sources)
        v = compute_verdict(claim, sources)
        report.append(ClaimReport(claim=claim, **v))
    return CheckResult(
        checked=len(report), backend=retriever.backend, classifier=classifier, report=report
    )
