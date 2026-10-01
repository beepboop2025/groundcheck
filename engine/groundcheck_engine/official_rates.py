"""Exact, dated NY Fed benchmark comparisons; no forecasting or implicit dates."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import re

import httpx

from .models import Source

BASE = "https://markets.newyorkfed.org/api/rates"
RATE_PATHS = {"SOFR": "secured/sofr", "EFFR": "unsecured/effr", "OBFR": "unsecured/obfr"}
_RATE = r"(?P<rate>SOFR|EFFR|OBFR)"
_VALUE = r"(?P<value>[0-9]{1,3}(?:\.[0-9]{1,6})?)\s*%"
_DATE = r"(?P<day>[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4}|[A-Za-z]{3,9}\s+[0-9]{1,2},?\s+[0-9]{4})"
_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    rf"(?:The\s+)?{_RATE}\s+(?:rate\s+)?(?:was|is|equals|equalled)\s+{_VALUE}\s+(?:on|for|as of)\s+{_DATE}[.]?",
    rf"(?:The\s+)?{_RATE}\s+(?:rate\s+)?(?:on|for|as of)\s+{_DATE}\s+(?:was|is|equals|equalled)\s+{_VALUE}[.]?",
    rf"On\s+{_DATE},?\s+(?:the\s+)?{_RATE}\s+(?:rate\s+)?(?:was|is|equals|equalled)\s+{_VALUE}[.]?",
))


@dataclass(frozen=True)
class RateClaim:
    rate: str
    day: str
    value: Decimal


def parse_claim(claim: str) -> RateClaim | None:
    """Match the complete atomic sentence; ambiguous dates remain unsupported."""
    for pattern in _PATTERNS:
        match = pattern.fullmatch(claim.strip())
        if not match:
            continue
        day = None
        for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"):
            try:
                day = datetime.strptime(match['day'].replace(',', ''), fmt).date()
                break
            except ValueError:
                continue
        if day is None or day > datetime.now(timezone.utc).date():
            return None
        value = Decimal(match['value'])
        if not 0 <= value <= 100:
            return None
        return RateClaim(match['rate'].upper(), day.isoformat(), value)
    return None


async def compare(claim: RateClaim) -> list[Source]:
    """Fetch exactly one official effective date, refusing duplicates and gaps."""
    url = f"{BASE}/{RATE_PATHS[claim.rate]}/search.json"
    async with httpx.AsyncClient(timeout=12, follow_redirects=False,
                                 headers={"User-Agent": "Groundcheck/0.7.2 (https://groundcheck.seiche.info)"}) as client:
        response = await client.get(url, params={"startDate": claim.day, "endDate": claim.day})
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise ValueError("Official rate response exceeds the bounded limit")
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Official rate response is not an object")
    records = payload.get("refRates")
    if not isinstance(records, list):
        raise ValueError("Official rate response has no refRates array")
    selected = [row for row in records if isinstance(row, dict)
                and row.get("effectiveDate") == claim.day and row.get("type") == claim.rate]
    if len(selected) != 1:
        raise ValueError("Official print is missing or ambiguous for this date")
    raw = selected[0].get("percentRate")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("Official percentRate is not a number")
    try:
        observed = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError("Official percentRate is invalid") from exc
    if not observed.is_finite() or not 0 <= observed <= 100:
        raise ValueError("Official percentRate is outside the supported range")
    retrieved = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(response.content).hexdigest()
    return [Source(
        title=f"Federal Reserve Bank of New York: {claim.rate}, {claim.day}",
        url=str(response.url),
        snippet=(f"NY Fed official {claim.rate} print for effective date {claim.day}: "
                 f"percentRate = {observed}%. Retrieved {retrieved}; response SHA-256 {digest}. "
                 "This is the currently published record, not an original-publication vintage or an executable quote."),
        stance="supports" if observed == claim.value else "refutes",
    )]
