"""x402 — machine-payable claim checking (USDC per call, no account).

The engine's public surface stays free (/verify, /search, the landing demo).
This module adds a machine-payable lane for the expensive batch endpoint
(/check): AI agents with wallets pay per call over the x402 protocol
(HTTP 402 + a signed stablecoin transfer authorization, verified and settled
through a facilitator). No signup, no API key, one call at a time.

Speaks both protocol generations, because the ecosystem is mid-migration:

  v1  402 JSON body {"x402Version": 1, "accepts": [...]}; payment arrives in
      an X-PAYMENT header; receipt leaves in X-PAYMENT-RESPONSE. Human
      network names ("base").
  v2  the same requirements also go out base64-encoded in a PAYMENT-REQUIRED
      header; payment may instead arrive in PAYMENT-SIGNATURE; the receipt
      also leaves in PAYMENT-RESPONSE. CAIP-2 network ids ("eip155:8453").

A 402 from this module always carries BOTH representations, and payment is
accepted from EITHER header, so v1 and v2 clients are equally served.

OFF BY DEFAULT and fail-closed at every step: the feature only exists when
GROUNDCHECK_X402_PAY_TO is set, and any decode/verify/settle failure returns
402 — a result is never served on an unverified or unsettled payment, and a
payment is never settled for a failed result.

Env (operator dials):
  GROUNDCHECK_X402_PAY_TO           receiving address (required; empty = off)
  GROUNDCHECK_X402_NETWORK          "base" (default) | "base-sepolia" |
                                    "polygon" | "arbitrum" | a CAIP-2 id
  GROUNDCHECK_X402_FACILITATOR      default https://x402.org/facilitator
                                    (testnet-only; point at a mainnet
                                    facilitator, e.g. CDP, to charge real USDC)
  GROUNDCHECK_X402_FACILITATOR_AUTH optional Authorization header value for
                                    facilitators that require one
  GROUNDCHECK_X402_ASSET            ERC-20 address; defaults to the canonical
                                    USDC deployment for the chosen network

Prices per endpoint live in config.X402_PRICES_USD; the free daily quota in
config.X402_FREE_PER_DAY. Implements the "exact" scheme; the facilitator API
surface is two POSTs (/verify and /settle).
"""

from __future__ import annotations

import base64
import binascii
import json
import os

import httpx

from . import config

_ASSET_DECIMALS = 6          # USDC
_TIMEOUT_S = 15
_MAX_PAYMENT_HEADER_B = 8192

# v1 network name -> (CAIP-2 id, canonical USDC deployment)
_NETWORKS = {
    "base":         ("eip155:8453",  "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"),
    "base-sepolia": ("eip155:84532", "0x036CbD53842c5426634e7929541eC2318f3dCF7e"),
    "polygon":      ("eip155:137",   "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"),
    "arbitrum":     ("eip155:42161", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"),
    "ethereum":     ("eip155:1",     "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"),
}
_CAIP2_TO_NAME = {caip2: name for name, (caip2, _) in _NETWORKS.items()}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def enabled() -> bool:
    return bool(_env("GROUNDCHECK_X402_PAY_TO"))


def price_usd(path: str | None) -> float | None:
    """Price for an endpoint path, or None if it is not payable."""
    if not path:
        return None
    return config.X402_PRICES_USD.get(path)


def _network_pair() -> tuple[str, str]:
    """(v1 name, CAIP-2 id) for the configured network."""
    raw = _env("GROUNDCHECK_X402_NETWORK", "base")
    if ":" in raw:  # operator gave a CAIP-2 id directly
        return _CAIP2_TO_NAME.get(raw, raw), raw
    caip2 = _NETWORKS.get(raw, (raw, ""))[0]
    return raw, caip2


def _asset() -> str:
    override = _env("GROUNDCHECK_X402_ASSET")
    if override:
        return override
    name, _ = _network_pair()
    return _NETWORKS.get(name, _NETWORKS["base"])[1]


def _atomic(usd: float) -> str:
    return str(int(round(usd * 10 ** _ASSET_DECIMALS)))


# Bazaar cataloging metadata, embedded in the 402 per the CDP discovery layer's
# observed convention ("extensions.bazaar on the 402"): route + example I/O +
# input schema. This is what makes the endpoint indexable/searchable by agents
# once a settle has flowed through a Bazaar-aware facilitator.
_BAZAAR_EXTENSIONS = {
    "/check": {
        "bazaar": {
            "routeTemplate": "/check",
            "info": {
                "input": {
                    "type": "http", "method": "POST", "bodyType": "json",
                    "body": {
                        "text": "The Eiffel Tower was completed in 1889. It is "
                                "330 metres tall.",
                        "max_claims": 8,
                    },
                },
                "output": {
                    "type": "json",
                    "example": {
                        "checked": 2,
                        "backend": "wikipedia+gdelt",
                        "classifier": "free-llm-router",
                        "report": [{
                            "claim": "The Eiffel Tower was completed in 1889.",
                            "verdict": "supported",
                            "confidence": 0.83,
                            "rationale": "sources support and none refute",
                        }],
                    },
                },
            },
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "text": {"type": "string", "minLength": 1,
                             "description": "Document whose factual claims to verify"},
                    "max_claims": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["text"],
            },
        }
    }
}


def _requirements(path: str, resource: str, network: str) -> dict:
    """PaymentRequirements for one call ("exact" scheme), for one network id."""
    usd = price_usd(path)
    if usd is None:
        raise ValueError(f"endpoint {path!r} has no x402 price")
    reqs = {
        "scheme": "exact",
        "network": network,
        "maxAmountRequired": _atomic(usd),
        "resource": resource,
        "description": "Fact-check for AI agents: verify every factual claim in "
                       "a document against live web sources (Wikipedia + world "
                       "news). Returns per-claim verdict (supported/refuted/"
                       "unverified), confidence, and rationale. Refuses to guess "
                       "on conflicting evidence — built for pre-answer grounding "
                       "and hallucination detection.",
        "mimeType": "application/json",
        "payTo": _env("GROUNDCHECK_X402_PAY_TO"),
        "maxTimeoutSeconds": 60,
        "asset": _asset(),
        "extra": {
            "name": _env("GROUNDCHECK_X402_ASSET_NAME", "USD Coin"),
            "version": _env("GROUNDCHECK_X402_ASSET_VERSION", "2"),
        },
    }
    if path in _BAZAAR_EXTENSIONS:
        reqs["extensions"] = _BAZAAR_EXTENSIONS[path]
    return reqs


def accepts(path: str, resource: str) -> list[dict]:
    """Both representations of the same offer: v1 network name + CAIP-2 id."""
    name, caip2 = _network_pair()
    out = [_requirements(path, resource, name)]
    if caip2 and caip2 != name:
        out.append(_requirements(path, resource, caip2))
    return out


def payment_required(path: str, resource: str, error: str) -> tuple[dict, dict]:
    """(v1 JSON body, extra response headers) for a 402.

    The body is the x402 v1 shape; the PAYMENT-REQUIRED header carries the
    v2 envelope so v2-only clients get first-class requirements too.
    """
    offers = accepts(path, resource)
    body = {"x402Version": 1, "error": error, "accepts": offers}
    v2 = {"x402Version": 2, "error": error, "accepts": offers[::-1]}  # CAIP-2 first
    headers = {"PAYMENT-REQUIRED": base64.b64encode(json.dumps(v2).encode()).decode()}
    return body, headers


def payment_header(request_headers) -> str | None:
    """The raw payment header, v1 or v2 transport, if any."""
    return request_headers.get("X-PAYMENT") or request_headers.get("PAYMENT-SIGNATURE")


def decode_payment(header: str | None) -> dict | None:
    """Payment header -> payload dict, or None on any malformation."""
    if not header or len(header) > _MAX_PAYMENT_HEADER_B:
        return None
    try:
        payload = json.loads(base64.b64decode(header, validate=True))
    except (ValueError, binascii.Error):
        return None
    return payload if isinstance(payload, dict) else None


def select_requirements(payment: dict, path: str, resource: str) -> dict:
    """The offer matching the network the payer signed for (else the first)."""
    offers = accepts(path, resource)
    net = payment.get("network")
    for offer in offers:
        if offer["network"] == net:
            return offer
    return offers[0]


def _facilitator_post(path: str, body: dict) -> dict:
    url = _env("GROUNDCHECK_X402_FACILITATOR", "https://x402.org/facilitator").rstrip("/")
    headers = {}
    auth = _env("GROUNDCHECK_X402_FACILITATOR_AUTH")
    if auth:
        headers["Authorization"] = auth
    r = httpx.post(f"{url}{path}", json=body, headers=headers, timeout=_TIMEOUT_S)
    r.raise_for_status()
    out = r.json()
    if not isinstance(out, dict):
        raise ValueError("facilitator returned a non-object body")
    return out


def _envelope(payment: dict, reqs: dict) -> dict:
    return {
        "x402Version": payment.get("x402Version", 1),
        "paymentPayload": payment,
        "paymentRequirements": reqs,
    }


def verify(payment: dict, reqs: dict) -> tuple[bool, str]:
    """Ask the facilitator whether the signed payment satisfies `reqs`."""
    try:
        out = _facilitator_post("/verify", _envelope(payment, reqs))
    except Exception as e:  # network, HTTP, JSON — all fail closed
        return False, f"facilitator verify unavailable: {type(e).__name__}"
    if out.get("isValid") is True:
        return True, ""
    return False, str(out.get("invalidReason") or "payment invalid")


def settle(payment: dict, reqs: dict) -> tuple[bool, dict]:
    """Settle on-chain via the facilitator. Fail-closed: no settle, no result."""
    try:
        out = _facilitator_post("/settle", _envelope(payment, reqs))
    except Exception as e:
        return False, {"success": False,
                       "errorReason": f"facilitator settle unavailable: {type(e).__name__}"}
    if out.get("success") is True:
        return True, out
    return False, out


def receipt_headers(receipt: dict) -> dict:
    """Settlement receipt in both header dialects (base64 JSON, per spec)."""
    encoded = base64.b64encode(json.dumps(receipt).encode()).decode()
    return {"X-PAYMENT-RESPONSE": encoded, "PAYMENT-RESPONSE": encoded}


def manifest(base_url: str) -> dict:
    """Machine-readable service manifest for GET /.well-known/x402.

    Lets agents (and discovery indexes) learn what is payable here, at what
    price, on which networks, without triggering a paid call first.
    """
    name, caip2 = _network_pair()
    return {
        "x402Versions": [1, 2],
        "service": "groundcheck",
        "description": "Verify factual claims against live sources: verdict, "
                       "confidence, citations. Free single-claim endpoint; "
                       "machine-payable batch document checking.",
        "payTo": _env("GROUNDCHECK_X402_PAY_TO"),
        "network": {"name": name, "caip2": caip2},
        "asset": _asset(),
        "freePerDay": config.X402_FREE_PER_DAY,
        "resources": [
            {
                "path": path,
                "method": "POST",
                "priceUSD": usd,
                "accepts": accepts(path, f"{base_url}{path}"),
            }
            for path, usd in sorted(config.X402_PRICES_USD.items())
        ],
        "alwaysFree": ["/verify", "/search", "/health"],
    }
