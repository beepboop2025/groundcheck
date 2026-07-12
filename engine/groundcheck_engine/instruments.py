"""Instrument identity via open symbology (Bloomberg OpenFIGI v3).

Two jobs:
  * resolve_instrument(): the sellable enrichment — map a ticker / ISIN /
    CUSIP / SEDOL / FIGI / free-text name to canonical FIGI records, with
    provenance attached (source, retrieval time, API version).
  * find_identifiers(): conservative extraction of EXPLICIT security
    identifiers from claim text ($AAPL cashtags, ISINs, FIGIs), so /verify can
    entity-resolve what a claim names before verifying it. Plain company names
    are deliberately NOT extracted — precision over recall; a claim mentioning
    "Apple pie" must never grow a stock ticker.

OpenFIGI is free: keyless 25 req/min; with a free key (OPENFIGI_API_KEY)
250 req/min and 100 jobs/request. V2 retired 2026-07-01 — v3 only.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import httpx

from . import config
from .models import Instrument, InstrumentProvenance, ResolveResult

_UA = "groundcheck/0.3 (+https://groundcheck.seiche.info)"

# ---- identifier shapes ---------------------------------------------------------
# ISIN: 2 letters, 9 alphanumerics, 1 check digit. CUSIP: 9 alphanumerics.
# FIGI: BBG + 9 alphanumerics (no vowels in practice; keep it loose).
# SEDOL: 7 alphanumerics — too collision-prone to auto-detect; explicit only.
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_FIGI = re.compile(r"^BBG[A-HJ-NP-Z0-9]{9}$")
_CUSIP = re.compile(r"^[0-9A-Z]{9}$")
_TICKERISH = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# In free text, only unambiguous spellings count: cashtags, exchange-qualified
# tickers, and bare ISINs / FIGIs. (A bare CUSIP in prose is indistinguishable
# from a serial number — explicit id_type only.)
_TEXT_CASHTAG = re.compile(r"\$([A-Z][A-Z0-9.\-]{0,9})\b")
_TEXT_EXCH_TICKER = re.compile(r"\b(?:NYSE|NASDAQ|LSE|AMEX)\s*:\s*([A-Z][A-Z0-9.\-]{0,9})\b")
_TEXT_ISIN = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
_TEXT_FIGI = re.compile(r"\b(BBG[A-HJ-NP-Z0-9]{9})\b")

_ID_TYPES = {"TICKER", "ID_ISIN", "ID_CUSIP", "ID_SEDOL", "ID_BB_GLOBAL"}


def detect_id_type(value: str) -> Optional[str]:
    """Best-effort id-type from the value's shape; None means 'search by text'."""
    v = value.strip().upper()
    if _FIGI.match(v):
        return "ID_BB_GLOBAL"
    if _ISIN.match(v):
        return "ID_ISIN"
    if _CUSIP.match(v) and any(ch.isdigit() for ch in v):
        return "ID_CUSIP"
    if _TICKERISH.match(v) and v == value.strip():  # already uppercase in source
        return "TICKER"
    return None


def find_identifiers(text: str) -> List[Tuple[str, str]]:
    """Explicit (id_type, value) references in free text, deduped, order kept."""
    out: List[Tuple[str, str]] = []
    seen = set()

    def add(id_type: str, value: str) -> None:
        key = (id_type, value.upper())
        if key not in seen:
            seen.add(key)
            out.append((id_type, value.upper()))

    for m in _TEXT_FIGI.finditer(text):
        add("ID_BB_GLOBAL", m.group(1))
    for m in _TEXT_ISIN.finditer(text):
        if not _TEXT_FIGI.match(m.group(1)):
            add("ID_ISIN", m.group(1))
    for m in _TEXT_CASHTAG.finditer(text):
        add("TICKER", m.group(1))
    for m in _TEXT_EXCH_TICKER.finditer(text):
        add("TICKER", m.group(1))
    return out[:4]  # a claim naming more instruments than this isn't one claim


# ---- OpenFIGI calls ------------------------------------------------------------

def _headers() -> dict:
    h = {"Content-Type": "application/json", "user-agent": _UA}
    if config.OPENFIGI_API_KEY:
        h["X-OPENFIGI-APIKEY"] = config.OPENFIGI_API_KEY
    return h


def _to_instrument(rec: dict) -> Instrument:
    return Instrument(
        figi=rec.get("figi") or "",
        name=rec.get("name"),
        ticker=rec.get("ticker"),
        exch_code=rec.get("exchCode"),
        security_type=rec.get("securityType"),
        market_sector=rec.get("marketSector"),
        composite_figi=rec.get("compositeFIGI"),
        share_class_figi=rec.get("shareClassFIGI"),
        description=rec.get("securityDescription"),
    )


async def _mapping(id_type: str, value: str) -> List[dict]:
    async with httpx.AsyncClient(timeout=10, headers=_headers()) as c:
        r = await c.post(f"{config.OPENFIGI_URL}/v3/mapping",
                         json=[{"idType": id_type, "idValue": value}])
        r.raise_for_status()
        body = r.json()
    return (body[0].get("data") or []) if body else []


async def _search(query: str, max_results: int) -> List[dict]:
    async with httpx.AsyncClient(timeout=10, headers=_headers()) as c:
        r = await c.post(f"{config.OPENFIGI_URL}/v3/search", json={"query": query})
        r.raise_for_status()
        body = r.json()
    return (body.get("data") or [])[:max_results]


# ---- tiny TTL cache (instrument identity is stable; save the rate limit) -------
_cache: "OrderedDict[str, Tuple[float, ResolveResult]]" = OrderedDict()
_CACHE_MAX = 4096
_lock = asyncio.Lock()


def _cache_get(key: str) -> Optional[ResolveResult]:
    if config.RESOLVE_CACHE_TTL_S <= 0:
        return None
    rec = _cache.get(key)
    if rec is None:
        return None
    expiry, value = rec
    if time.time() > expiry:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return value


def _cache_put(key: str, value: ResolveResult) -> None:
    if config.RESOLVE_CACHE_TTL_S <= 0:
        return
    _cache[key] = (time.time() + config.RESOLVE_CACHE_TTL_S, value)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def _provenance() -> InstrumentProvenance:
    return InstrumentProvenance(
        source="OpenFIGI (Bloomberg open symbology)",
        url=f"{config.OPENFIGI_URL}/v3",
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        authenticated=bool(config.OPENFIGI_API_KEY),
    )


async def resolve_instrument(query: str, id_type: Optional[str] = None,
                             max_results: int = 5) -> ResolveResult:
    """Canonical instrument records for a query, cache-first, provenance always."""
    q = query.strip()
    declared = id_type.upper() if id_type else None
    if declared is not None and declared not in _ID_TYPES:
        return ResolveResult(query=q, id_type=id_type, matched=False, instruments=[],
                             provenance=_provenance(),
                             note=f"unknown id_type {id_type!r}; use one of {sorted(_ID_TYPES)}")

    effective = declared or detect_id_type(q)
    key = f"{effective or 'SEARCH'}|{q.upper()}|{max_results}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        if effective is not None:
            records = await _mapping(effective, q.upper())
            # A ticker that maps to nothing is often a name fragment — fall
            # through to keyword search rather than answering "not found".
            if not records and effective == "TICKER" and declared is None:
                effective = None
        if effective is None:
            records = await _search(q, max_results)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            return ResolveResult(query=q, id_type=effective, matched=False, instruments=[],
                                 provenance=_provenance(),
                                 note="OpenFIGI rate limit hit — retry shortly "
                                      "(set OPENFIGI_API_KEY for 10x the limit)")
        return ResolveResult(query=q, id_type=effective, matched=False, instruments=[],
                             provenance=_provenance(),
                             note=f"OpenFIGI error HTTP {exc.response.status_code}")
    except httpx.HTTPError as exc:
        return ResolveResult(query=q, id_type=effective, matched=False, instruments=[],
                             provenance=_provenance(),
                             note=f"OpenFIGI unreachable: {type(exc).__name__}")

    instruments = [_to_instrument(r) for r in records[:max_results] if r.get("figi")]
    result = ResolveResult(
        query=q,
        id_type=effective,
        matched=bool(instruments),
        instruments=instruments,
        provenance=_provenance(),
        note=None if instruments else "no instrument found in open symbology",
    )
    async with _lock:
        _cache_put(key, result)
    return result
