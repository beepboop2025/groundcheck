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

from . import config, x402
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
    # Paid x402 calls bought their capacity; only the free surface is limited.
    if request.method == "POST" and not getattr(request.state, "x402_paid", False):
        ip = request.client.host if request.client else "anon"
        now = time.time()
        dq = _hits[ip]
        while dq and now - dq[0] > _RL_WINDOW:
            dq.popleft()
        if len(dq) >= _RL_MAX:
            return JSONResponse({"error": "rate limited — try again in a minute"}, status_code=429)
        dq.append(now)
    return await call_next(request)


# x402 free-tier ledger: ip -> [utc day number, calls used]. Best-effort per
# warm instance, same as the rate limiter above.
_free_used: Dict[str, List[int]] = {}


def _free_quota_take(ip: str) -> int | None:
    """Consume one free paid-endpoint call; remaining count, or None if dry."""
    if config.X402_FREE_PER_DAY <= 0:
        return None
    day = int(time.time() // 86400)
    rec = _free_used.get(ip)
    if rec is None or rec[0] != day:
        rec = [day, 0]
        _free_used[ip] = rec
    if rec[1] >= config.X402_FREE_PER_DAY:
        return None
    rec[1] += 1
    return config.X402_FREE_PER_DAY - rec[1]


def _pay_402(path: str, resource: str, error: str) -> JSONResponse:
    body, headers = x402.payment_required(path, resource, error)
    return JSONResponse(body, status_code=402, headers=headers)


# Registered after rate_limit, so it runs OUTSIDE it: payment is decided
# first, and verified payers skip the free-surface rate limit entirely.
@app.middleware("http")
async def x402_gate(request: Request, call_next):
    path = request.url.path
    if not (x402.enabled() and request.method == "POST"
            and x402.price_usd(path) is not None):
        return await call_next(request)

    resource = str(request.url)
    raw = x402.payment_header(request.headers)

    if raw is None:  # unpaid: free daily quota, then 402 with the offer
        ip = request.client.host if request.client else "anon"
        remaining = _free_quota_take(ip)
        if remaining is None:
            return _pay_402(path, resource,
                            "free daily quota exhausted — pay per call via x402")
        resp = await call_next(request)
        resp.headers["X-Groundcheck-Free-Remaining"] = str(remaining)
        return resp

    payment = x402.decode_payment(raw)
    if payment is None:
        return _pay_402(path, resource, "payment header malformed")
    reqs = x402.select_requirements(payment, path, resource)
    ok, why = x402.verify(payment, reqs)
    if not ok:
        return _pay_402(path, resource, why)

    request.state.x402_paid = True
    resp = await call_next(request)
    if resp.status_code != 200:
        return resp  # engine failed: serve the error, charge nothing

    settled, receipt = x402.settle(payment, reqs)
    if not settled:  # fail-closed: no settle, no result
        return _pay_402(path, resource,
                        str(receipt.get("errorReason") or "settlement failed"))
    for k, v in x402.receipt_headers(receipt).items():
        resp.headers[k] = v
    return resp


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
    return {"status": "ok", "backend": retriever.backend, "version": app.version,
            "x402": x402.enabled()}


@app.get("/.well-known/x402")
async def x402_manifest(request: Request) -> JSONResponse:
    """Machine-readable payment manifest, for agents and discovery indexes."""
    if not x402.enabled():
        return JSONResponse({"error": "x402 not enabled on this deployment"},
                            status_code=404)
    return JSONResponse(x402.manifest(str(request.base_url).rstrip("/")))


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
