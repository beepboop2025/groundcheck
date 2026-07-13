"""FastAPI app exposing the Groundcheck engine.

Endpoints:
  GET  /            interactive landing page / live demo (HTML)
  GET  /health      liveness + which retrieval backend is active
  GET  /search?q=&n=  the documented GROUNDCHECK_SEARCH_URL contract ({results:[...]})
  POST /verify      retrieve -> classify stance -> verdict for one claim
  POST /check       extract claims from text and verify each
  GET  /attest/pubkey  Ed25519 public key + how to verify response receipts
  GET/POST /mcp     streamable-HTTP MCP transport (the 3 tools, by URL)
"""
import asyncio
import re
import time
from collections import Counter, OrderedDict, defaultdict, deque
from importlib import resources
from typing import Any, Deque, Dict, List, Tuple

from fastapi import Body, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import attest, config, instruments, mcp_http, x402
from .landing import LANDING_HTML
from .models import (CheckResult, ClaimInstrument, ClaimReport, ResolveResult,
                     Source, VerifyResult)
from .retrieval import Retriever
from .stance import classify_stances
from .verdict import compute_verdict

app = FastAPI(
    title="Groundcheck Engine",
    version="0.5.0",
    contact={
        "name": "Groundcheck",
        "url": "https://github.com/beepboop2025/groundcheck",
        "email": "mrinallovesbhature@gmail.com",
    },
)
retriever = Retriever()


def _deref(node, defs, depth=0):
    """Inline $ref schemas: discovery scanners (x402scan) read operation
    schemas literally and treat a bare $ref as 'missing input schema'."""
    if depth > 12 or not isinstance(node, (dict, list)):
        return node
    if isinstance(node, list):
        return [_deref(v, defs, depth + 1) for v in node]
    ref = node.get("$ref", "")
    if ref.startswith("#/components/schemas/"):
        target = defs.get(ref.rsplit("/", 1)[-1], {})
        merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        return _deref(merged, defs, depth + 1)
    return {k: _deref(v, defs, depth + 1) for k, v in node.items()}


def _custom_openapi() -> dict:
    """Mark /check as x402-paid and everything else as free (security: []) so
    discovery indexes probe only the paid surface."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = _base_openapi()
    defs = schema.get("components", {}).get("schemas", {})
    schema["paths"] = _deref(schema.get("paths", {}), defs)
    schema["info"]["x-guidance"] = (
        "POST /verify with {claim} to ground one factual claim (free, rate "
        "limited). POST /check with {text} to extract and verify every claim "
        "in a document (x402 paid, small free daily quota). Both return "
        "verdicts (supported/refuted/unverified), confidence scores, and "
        "cited sources. POST /resolve with {query} to map a ticker/ISIN/"
        "CUSIP/FIGI/name to canonical instrument identity with provenance "
        "(x402 paid, same free quota)."
    )
    schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schemes["x402"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-PAYMENT",
        "description": "x402 payment payload (v1 X-PAYMENT or v2 PAYMENT-SIGNATURE). "
                       "Unpaid calls receive HTTP 402 with an accepts[] offer.",
    }
    for route_path, item in schema.get("paths", {}).items():
        for op in item.values():
            if not isinstance(op, dict):
                continue
            price = x402.price_usd(route_path)
            if price is not None:
                op["security"] = [{"x402": []}]
                op.setdefault("responses", {})["402"] = {
                    "description": "Payment Required"}
                op["x-payment-info"] = {
                    "price": {"mode": "fixed", "currency": "USD",
                              "amount": f"{price:.6f}"},
                    "protocols": [{"x402": {}}],
                }
            else:
                op["security"] = []
    app.openapi_schema = schema
    return schema


_base_openapi = app.openapi
app.openapi = _custom_openapi

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_FIRST_PERSON = re.compile(r"^(i|we|you|my|our|your|let's|let us)\b", re.I)
# A checkable claim needs at least one factual anchor: a number, a copula/verb
# of fact, or a multi-word proper noun. Questions and opinions are skipped.
_FACTUAL_SIGNAL = re.compile(
    r"\d|[A-Z][a-z]+ [A-Z][a-z]+|\b(is|are|was|were|has|have|had|will|did|does|"
    r"located|founded|born|died|costs?|measures?|won|launched|released|completed|"
    r"discovered|invented|announced)\b"
)

# Best-effort per-IP rate limit on the public demo, to protect free LLM quota.
# (Serverless instances are ephemeral, so this caps bursts per warm instance.)
_RL_WINDOW = 60.0
_RL_MAX = 30
_hits: Dict[str, Deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    """Real client behind a reverse proxy (Caddy/Vercel set X-Forwarded-For).
    Without this, every public caller shares one 127.0.0.1 quota bucket —
    one visitor's testing exhausts everyone's free tier."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "anon"


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    # Paid x402 calls bought their capacity; only the free surface is limited.
    if request.method == "POST" and not getattr(request.state, "x402_paid", False):
        ip = _client_ip(request)
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
    if not (x402.enabled() and x402.price_usd(path) is not None):
        return await call_next(request)

    resource = str(request.url)

    # Paywall answers before method/body validation: discovery probes (x402scan,
    # Bazaar indexers) hit paid paths with GET and must see the 402 offer, not a
    # 405 from the router. Never consumes quota; settle only happens on POST.
    if request.method != "POST":
        return _pay_402(path, resource, "payment required (call with POST)")

    raw = x402.payment_header(request.headers)

    if raw is None:  # unpaid: free daily quota, then 402 with the offer
        ip = _client_ip(request)
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


@app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
async def llms_txt() -> str:
    return resources.files("groundcheck_engine").joinpath("llms.txt").read_text()


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    data = resources.files("groundcheck_engine").joinpath("favicon.ico").read_bytes()
    return Response(data, media_type="image/x-icon",
                    headers={"cache-control": "public, max-age=86400"})


class VerifyRequest(BaseModel):
    claim: str
    max_sources: int = Field(5, ge=1, le=10)


class CheckRequest(BaseModel):
    text: str
    max_claims: int = Field(8, ge=1, le=20)


class ResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    id_type: str | None = Field(
        None, description="TICKER | ID_ISIN | ID_CUSIP | ID_SEDOL | ID_BB_GLOBAL; "
                          "auto-detected from the value's shape when omitted")
    max_results: int = Field(5, ge=1, le=10)


def _extract_claims(text: str, max_claims: int) -> List[str]:
    out: List[str] = []
    seen = set()
    for p in (p.strip() for p in _SENTENCE.split(text)):
        if len(p) <= 20 or p.endswith("?"):
            continue
        if _FIRST_PERSON.match(p) or not _FACTUAL_SIGNAL.search(p):
            continue
        key = re.sub(r"\W+", " ", p.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= max_claims:
            break
    return out


# ---- verdict cache: repeat claims are answered from memory --------------------
_verdict_cache: "OrderedDict[str, Tuple[float, VerifyResult]]" = OrderedDict()
_CACHE_MAX = 2048


def _cache_get(key: str) -> VerifyResult | None:
    if config.CACHE_TTL_S <= 0:
        return None
    rec = _verdict_cache.get(key)
    if rec is None:
        return None
    expiry, value = rec
    if time.time() > expiry:
        _verdict_cache.pop(key, None)
        return None
    _verdict_cache.move_to_end(key)
    return value


def _cache_put(key: str, value: VerifyResult) -> None:
    if config.CACHE_TTL_S <= 0:
        return
    _verdict_cache[key] = (time.time() + config.CACHE_TTL_S, value)
    _verdict_cache.move_to_end(key)
    while len(_verdict_cache) > _CACHE_MAX:
        _verdict_cache.popitem(last=False)


async def _resolve_claim_instruments(claim: str) -> List[ClaimInstrument]:
    """Entity-resolve EXPLICIT security references ($AAPL, ISINs, FIGIs) named
    by a claim, so 'which instrument is this about' is answered canonically
    before the claim is judged. Conservative on purpose: no reference, no call."""
    if not config.VERIFY_RESOLVES_INSTRUMENTS:
        return []
    refs = instruments.find_identifiers(claim)
    if not refs:
        return []
    out: List[ClaimInstrument] = []
    for id_type, value in refs:
        r = await instruments.resolve_instrument(value, id_type, max_results=1)
        out.append(ClaimInstrument(
            reference=value, id_type=id_type, resolved=r.matched,
            instrument=r.instruments[0] if r.instruments else None))
    return out


async def _verify_claim(claim: str, max_sources: int) -> Tuple[VerifyResult, bool]:
    """Shared retrieve → classify → verdict path. Returns (result, was_cached)."""
    key = f"{max_sources}|{' '.join(claim.lower().split())}"
    cached = _cache_get(key)
    if cached is not None:
        return cached, True
    resolved, (sources, classifier) = await asyncio.gather(
        _resolve_claim_instruments(claim),
        _search_and_classify(claim, max_sources),
    )
    v = compute_verdict(claim, sources)
    rationale = v.pop("rationale")
    unresolved = [ci.reference for ci in resolved if not ci.resolved]
    if unresolved:
        rationale += (
            " · instrument check: "
            + ", ".join(f"{r!r} does not resolve in open symbology (OpenFIGI)"
                        for r in unresolved))
    result = VerifyResult(
        claim=claim, backend=retriever.backend, classifier=classifier,
        sources=sources, rationale=rationale, instruments=resolved, **v
    )
    # Signed receipt over the verdict. Attached before caching, so a cache hit
    # re-serves the original receipt (signed_at = when the verdict was made).
    # Never breaks the endpoint: failures come back as {attested: false}.
    result.attestation = attest.attest_verify_response(result.model_dump())
    # A router outage is transient — never freeze it into the cache.
    if classifier != "error":
        _cache_put(key, result)
    return result, False


async def _search_and_classify(claim: str, max_sources: int):
    sources = await retriever.search(claim, max_sources)
    classifier = await classify_stances(claim, sources)
    return sources, classifier


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "backend": retriever.backend, "version": app.version,
            "x402": x402.enabled()}


@app.get("/attest/pubkey")
async def attest_pubkey() -> JSONResponse:
    """Public half of response attestation: the Ed25519 key receipts are
    signed with, the message format, and how to verify a receipt offline."""
    try:
        mode = attest.key_mode()
        info = {
            "public_key": attest.public_key_hex(),
            "algo": attest.ALGO,
            "domain": attest.DOMAIN,
            "key_mode": mode,
            "message_format": attest.MESSAGE_FORMAT,
            "canonicalization": attest.CANONICALIZATION,
            "manifests": {
                "verify": "keys: backend (response.backend), claim_sha256 "
                          "(sha256 hex of response.claim, utf-8), confidence, "
                          "model (response.classifier), signed_at "
                          "(receipt.signed_at), source_urls "
                          "(response.sources[].url, response order), verdict",
                "check": "keys: backend, checked, claims ([{claim_sha256, "
                          "verdict, confidence}] from response.report, in "
                          "order), input_sha256 (attestation.input_sha256; "
                          "sha256 hex of the submitted text), model "
                          "(response.classifier), signed_at (receipt.signed_at)",
            },
            "howto": "Take the response JSON you were given. Rebuild the "
                     "manifest for its kind from the fields above, hash it as "
                     "canonical JSON (sorted keys, compact separators, sha256 "
                     "hex), check it equals receipt.manifest_hash, then verify "
                     "receipt.sig over the message "
                     f"'{attest.MESSAGE_FORMAT}' with any Ed25519 library "
                     "using receipt.public_key. No call to Groundcheck needed.",
            "verify_example": attest.VERIFY_EXAMPLE,
        }
        if mode == "ephemeral":
            info["warning"] = (
                "This deployment signs with an ephemeral per-process key "
                "(GROUNDCHECK_ATTEST_KEY is not set). Receipts stay verifiable "
                "against the public key inside each receipt, but the signing "
                "identity changes on restart, so receipts cannot be tied to a "
                "long-lived operator identity.")
        return JSONResponse(info)
    except Exception as exc:
        return JSONResponse({"error": f"attestation unavailable: "
                                      f"{type(exc).__name__}: {exc}"},
                            status_code=503)


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
async def verify(req: VerifyRequest, response: Response) -> VerifyResult:
    result, cached = await _verify_claim(req.claim, req.max_sources)
    response.headers["X-Groundcheck-Cache"] = "hit" if cached else "miss"
    return result


@app.post("/resolve", response_model=ResolveResult)
async def resolve(req: ResolveRequest) -> ResolveResult:
    """Attested instrument-identity enrichment: canonical FIGI records (with
    provenance) for a ticker / ISIN / CUSIP / SEDOL / FIGI / name."""
    return await instruments.resolve_instrument(req.query, req.id_type, req.max_results)


# ---- MCP over HTTP -----------------------------------------------------------
# The npm package is a stdio server an agent installs; this is the endpoint an
# agent adds BY URL (Claude/ChatGPT/Cursor connectors, Smithery, Glama) with no
# install at all. Tools call the same functions the REST endpoints do, and the
# paid ones answer 402 with the same x402 offer.

async def _mcp_verify_claim(claim: str, max_sources: int = 5) -> dict:
    result, _ = await _verify_claim(claim, max(1, min(int(max_sources), 10)))
    return result.model_dump()


async def _mcp_check_citations(text: str, max_claims: int = 8) -> dict:
    result, _hits = await _check_text(text, max(1, min(int(max_claims), 20)))
    return result.model_dump()


async def _mcp_resolve_instrument(query: str, id_type: str | None = None,
                                  max_results: int = 5) -> dict:
    r = await instruments.resolve_instrument(query, id_type,
                                             max(1, min(int(max_results), 10)))
    return r.model_dump()


_MCP_HANDLERS = {
    "verify_claim": _mcp_verify_claim,
    "check_citations": _mcp_check_citations,
    "resolve_instrument": _mcp_resolve_instrument,
}


@app.get("/mcp", include_in_schema=False)
async def mcp_get() -> JSONResponse:
    """Probing clients GET the endpoint before speaking JSON-RPC."""
    return JSONResponse({
        "transport": "streamable-http",
        "protocolVersion": mcp_http.PROTOCOL_VERSION,
        "server": {"name": mcp_http.SERVER_NAME, "version": mcp_http.SERVER_VERSION},
        "usage": "POST JSON-RPC 2.0 here (initialize, tools/list, tools/call).",
        "paid_tools": sorted(mcp_http.TOOL_PRICED_AS),
    })


@app.post("/mcp", include_in_schema=False)
async def mcp_post(request: Request, body: Any = Body(default=None)) -> Response:
    if body is None:
        return JSONResponse(
            mcp_http._error(None, mcp_http.PARSE_ERROR, "empty or non-JSON body"),
            status_code=400)

    msgs = body if isinstance(body, list) else [body]
    if len(msgs) > mcp_http.MAX_BATCH:
        # one HTTP request costs one rate-limiter hit, so an unbounded batch
        # would evade both the ceiling and the paywall.
        return JSONResponse(
            mcp_http._error(None, mcp_http.INVALID_REQUEST,
                            f"batch too large (max {mcp_http.MAX_BATCH} messages)"),
            status_code=413)

    # A paid tool call is settled BEFORE it runs, exactly like the REST paths:
    # free daily quota first, then a 402 offer, then verify + settle or nothing.
    single = msgs[0] if len(msgs) == 1 and isinstance(msgs[0], dict) else None
    path = mcp_http.priced_tool(single) if single is not None else None
    if x402.enabled() and path is not None:
        resource = str(request.url)
        raw = x402.payment_header(request.headers)
        if raw is None:
            ip = _client_ip(request)
            if _free_quota_take(ip) is None:
                return _pay_402(path, resource,
                                f"{mcp_http.tool_name(single)} is a paid tool — "
                                "free daily quota exhausted")
        else:
            payment = x402.decode_payment(raw)
            if payment is None:
                return _pay_402(path, resource, "payment header malformed")
            reqs = x402.select_requirements(payment, path, resource)
            ok, why = x402.verify(payment, reqs)
            if not ok:
                return _pay_402(path, resource, why)
            resp = await mcp_http.dispatch(single, _MCP_HANDLERS)
            settled, receipt = x402.settle(payment, reqs)
            if not settled:  # fail-closed: no settle, no result
                return _pay_402(path, resource,
                                str(receipt.get("errorReason") or "settlement failed"))
            return JSONResponse(resp, headers=x402.receipt_headers(receipt))

    responses = []
    for m in msgs:
        r = await mcp_http.dispatch(m, _MCP_HANDLERS)
        if r is None:
            continue
        if x402.enabled() and isinstance(m, dict) and m.get("method") == "tools/list":
            r = mcp_http.annotate_tools_list(r)
        responses.append(r)

    if not responses:                       # notification-only body
        return Response(status_code=202)
    payload = responses if isinstance(body, list) else responses[0]
    return JSONResponse(payload)


async def _check_text(text: str, max_claims: int) -> Tuple[CheckResult, int]:
    """Extract claims and verify each. Shared by POST /check and the MCP tool."""
    claims = _extract_claims(text, max_claims)
    sem = asyncio.Semaphore(4)  # be polite to Wikipedia/GDELT and the LLM tier

    async def one(claim: str) -> Tuple[VerifyResult, bool]:
        async with sem:
            return await _verify_claim(claim, 4)

    results = await asyncio.gather(*(one(c) for c in claims))
    report = [
        ClaimReport(claim=r.claim, verdict=r.verdict, confidence=r.confidence,
                    rationale=r.rationale)
        for r, _ in results
    ]
    hits = sum(1 for _, cached in results if cached)
    tally = Counter(r.classifier for r, _ in results if r.classifier != "none")
    classifier = tally.most_common(1)[0][0] if tally else "none"
    result = CheckResult(
        checked=len(report), backend=retriever.backend, classifier=classifier, report=report
    )
    # Signed receipt over the whole report, bound to a hash of the input text.
    result.attestation = attest.attest_check_response(result.model_dump(), text)
    return result, hits


@app.post("/check", response_model=CheckResult)
async def check(req: CheckRequest, response: Response) -> CheckResult:
    result, hits = await _check_text(req.text, req.max_claims)
    response.headers["X-Groundcheck-Cache-Hits"] = str(hits)
    return result
