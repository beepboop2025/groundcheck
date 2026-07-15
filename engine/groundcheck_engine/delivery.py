"""Delivery verification — the neutral layer between an agent's payment and a
service's outcome (POST /attest-delivery).

WHY. In agentic commerce an agent pays a service over x402 and receives a
response, but nothing neutral records whether what arrived matches what was
advertised — the "payment–service decoupling" / accountability gap named by
the field's surveys (SoK: Blockchain Agent-to-Agent Payments, arXiv:2604.03733;
A402, arXiv:2603.01179; RAILS, arXiv:2606.08790). Groundcheck already grounds
content and signs receipts about its OWN answers; this module points the same
machinery at OTHER services' deliveries and returns an offline-verifiable
receipt binding three things that otherwise live in separate silos:

  payment   the x402 settlement receipt the buyer got (hash + decoded fields),
  delivery  the exact response bytes (sha256) against the advertised schema,
  content   grounded verdicts over the factual claims in the response.

The endpoint judges CONSISTENCY, not merit: "delivered as advertised and
nothing in it is contradicted by evidence" — never "this service is good".

WHAT LIVES HERE (pure, offline, no LLM): the structural schema-conformance
check, the settlement-receipt decoder, and the delivery-verdict rule. The
grounding pass reuses the /check pipeline (app.py) and the signature comes
from attest.py (kind "delivery"), same as every other receipt.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, List, Optional, Tuple

_MAX_DEPTH = 16               # schemas deeper than this are not walked further
_MAX_ITEMS_CHECKED = 50       # per array; conformance is a check, not an audit
_MAX_RECEIPT_B = 16384


# ---------------------------------------------------------------------------
# Structural schema conformance ("did you get the shape you paid for")
# ---------------------------------------------------------------------------
# A deliberate SUBSET of JSON Schema — type, required, properties, items, enum
# — implemented here rather than pulling in a full validator: the advertised
# schemas this checks come from 402 offers and Bazaar listings, which use
# exactly this subset, and a small auditable walker keeps the engine
# dependency-free. Unknown keywords are ignored, never failed on.

def _is_type(value: Any, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    return True  # unknown type name: tolerate, don't invent a failure


def conformance_problems(instance: Any, schema: Any,
                         path: str = "$", depth: int = 0) -> List[str]:
    """Structural mismatches between a delivered instance and an advertised
    schema. Empty list = conforms (within the documented subset)."""
    if depth > _MAX_DEPTH or not isinstance(schema, dict):
        return []
    problems: List[str] = []

    stype = schema.get("type")
    if stype:
        allowed = stype if isinstance(stype, list) else [stype]
        if not any(_is_type(instance, t) for t in allowed if isinstance(t, str)):
            problems.append(
                f"{path}: advertised type {stype!r}, delivered "
                f"{type(instance).__name__}")
            return problems  # deeper keywords are meaningless on a type miss

    enum = schema.get("enum")
    if isinstance(enum, list) and enum and instance not in enum:
        problems.append(f"{path}: value not in advertised enum")

    if isinstance(instance, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for req in required:
                if isinstance(req, str) and req not in instance:
                    problems.append(
                        f"{path}: missing advertised required property {req!r}")
        props = schema.get("properties")
        if isinstance(props, dict):
            for key, sub in props.items():
                if key in instance:
                    problems += conformance_problems(
                        instance[key], sub, f"{path}.{key}", depth + 1)

    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, v in enumerate(instance[:_MAX_ITEMS_CHECKED]):
                problems += conformance_problems(
                    v, items, f"{path}[{i}]", depth + 1)

    return problems


# ---------------------------------------------------------------------------
# x402 settlement-receipt decoding (payment binding)
# ---------------------------------------------------------------------------
def decode_settlement(raw: str) -> Tuple[Optional[dict], List[str]]:
    """Decode the settlement receipt a buyer got from a paid x402 call.

    Receipts travel base64(JSON) in X-PAYMENT-RESPONSE / PAYMENT-RESPONSE;
    raw JSON is accepted too (buyers often store the decoded form). Returns
    (fields, problems) — fields is None when nothing decodable arrived.
    Decoding binds, it does not prove: the attestation records what receipt
    the buyer PRESENTED; on-chain confirmation of the transaction hash is the
    verifier's own step (docs/delivery-attestation.md)."""
    if not raw or len(raw) > _MAX_RECEIPT_B:
        return None, ["settlement receipt missing or too large"]
    obj: Any = None
    try:
        obj = json.loads(base64.b64decode(raw, validate=True))
    except (ValueError, binascii.Error):
        try:
            obj = json.loads(raw)
        except ValueError:
            return None, ["settlement receipt is neither base64(JSON) nor JSON"]
    if not isinstance(obj, dict):
        return None, ["settlement receipt is not a JSON object"]
    return obj, []


def settlement_fields(obj: Optional[dict]) -> dict:
    """The stable, attestable subset of a decoded settlement receipt (both
    facilitator dialects: v1 `transaction`, some emit `txHash`)."""
    if not obj:
        return {"network": None, "transaction": None, "payer": None,
                "success": None}
    tx = obj.get("transaction") or obj.get("txHash")
    return {
        "network": obj.get("network"),
        "transaction": tx if isinstance(tx, str) else None,
        "payer": obj.get("payer") if isinstance(obj.get("payer"), str) else None,
        "success": obj.get("success") if isinstance(obj.get("success"), bool)
                   else None,
    }


# ---------------------------------------------------------------------------
# The delivery verdict
# ---------------------------------------------------------------------------
def derive_verdict(supported: int, refuted: int, unverified: int,
                   conformance_checked: bool,
                   conformance_valid: Optional[bool]) -> Tuple[str, str]:
    """(delivery_verdict, rationale) from the grounding tally + conformance.

    consistent    nothing refuted, and at least one positive signal (a
                  supported claim, or the advertised schema validating)
    degraded      a minority of claims refuted — content partially fails
    inconsistent  the delivery is NOT what was advertised: schema invalid,
                  or refuted claims match/outnumber supported ones
    unverifiable  nothing contradicted, but nothing confirmable either

    The rule is deliberately order-of-severity: a shape violation is judged
    before content, because a response that isn't even the advertised shape
    cannot be redeemed by containing some true sentences.
    """
    if conformance_checked and conformance_valid is False:
        return ("inconsistent",
                "delivered response does not conform to the advertised schema")
    if refuted > 0 and refuted >= supported:
        return ("inconsistent",
                f"{refuted} claim(s) in the delivered content are refuted by "
                f"evidence (vs {supported} supported)")
    if refuted > 0:
        return ("degraded",
                f"{refuted} of {supported + refuted + unverified} checked "
                "claim(s) refuted; the rest hold")
    if supported > 0:
        note = " and the advertised schema validates" \
            if conformance_checked and conformance_valid else ""
        return ("consistent",
                f"no refuted claims; {supported} claim(s) supported by "
                f"evidence{note}")
    if conformance_checked and conformance_valid:
        return ("consistent",
                "the advertised schema validates; content contains no "
                "evidence-checkable claims")
    return ("unverifiable",
            "nothing in the delivered content was contradicted, but nothing "
            "could be confirmed either (no supported claims, no schema to "
            "check against)")
