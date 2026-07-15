"""Evidence-bound provenance — bind the whole path, not just the verdict.

The attestation in attest.py signs WHAT Groundcheck said (verdict, confidence,
source URLs). Evidence-Bound Gateway-Path Provenance (arXiv:2606.22560) argues
that for a third party consuming a mediated inference, the URLs are not enough:
the client should be able to verify HOW the answer was reached — over which exact
evidence, with which stances, via which model route — so that swapping a source's
content, flipping a stance, or quietly changing the model cannot pass as the same
verdict.

This module builds that binding in software, in two pieces the paper names:

1. EVIDENCE CHAIN (their StreamEv). A rolling commitment over the ordered
   evidence: S_0 = H(domain), S_i = H(S_{i-1} || H(url || snippet || stance || i)).
   The final root S_n is bound into the signed manifest. Recomputable by any
   holder of the response (the sources, with their snippets and stances, are in
   the response), so it needs no secret — but tampering with ANY evidence item,
   its text, its stance, or its order changes the root and the receipt no longer
   verifies. This is the difference between "cited these URLs" and "reasoned over
   exactly this evidence."

2. ROUTE DESCRIPTOR. The gateway path: which model(s) answered (the ensemble
   panel), the retrieval backend, whether the claim was decomposed into atoms.
   Hashed and bound too, so a silent model or pipeline substitution is caught.

ATTESTATION MODE. The paper's full guarantee also binds the RUNTIME that
executed the path, via a hardware attestation quote (AWS Nitro). That needs a
TEE we do not run here, so provenance ships in `software` mode: the Ed25519
operator signature over the evidence-and-route-bound manifest (already in
attest.py). The `tee` mode — where the same manifest is additionally covered by
a quote binding the enclave measurement — is the documented upgrade, gated on a
quote being available (GROUNDCHECK_TEE_QUOTE), exactly the mock-vs-Nitro split
the paper uses in its own prototype. `software` mode proves operator + path;
`tee` mode would additionally prove the runtime. We never claim `tee` without a
quote.
"""
from __future__ import annotations

import hashlib
import os
from typing import List

PROV_DOMAIN = "groundcheck-provenance-v1"


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def evidence_item_commitment(url: str, snippet: str, stance: str | None, index: int) -> str:
    """Per-source leaf: binds the source's identity, its exact text, the stance
    taken on it, and its position. Any change flips this hash."""
    stance = stance or "none"
    return _h(f"{index}\x1f{url}\x1f{snippet}\x1f{stance}")


def evidence_chain(sources: List[dict]) -> dict:
    """Rolling commitment over the ordered evidence (StreamEv analog).

    Returns the per-item leaves (so a holder can see and recompute each) and the
    final root. Stub sources are excluded — they carry no evidence — matching
    what the verdict actually reasoned over.
    """
    leaves = []
    rolling = _h(PROV_DOMAIN)
    for i, s in enumerate(src for src in sources if not src.get("stub")):
        leaf = evidence_item_commitment(
            s.get("url", ""), s.get("snippet", ""), s.get("stance"), i)
        rolling = _h(rolling + leaf)
        leaves.append({"index": i, "url": s.get("url", ""),
                       "stance": s.get("stance"), "commitment": leaf})
    return {"root": rolling, "n_items": len(leaves), "items": leaves}


def route_descriptor(response: dict) -> dict:
    """The path the gateway followed — bound so a silent substitution is caught."""
    atoms = response.get("atoms")
    return {
        "model": response.get("classifier", ""),
        "backend": response.get("backend", ""),
        "ensembled": str(response.get("classifier", "")).startswith("ensemble:"),
        "decomposed": bool(atoms),
        "n_atoms": len(atoms) if atoms else 0,
        "certified": bool((response.get("guarantee") or {}).get("certified")),
    }


def route_hash(response: dict) -> str:
    r = route_descriptor(response)
    return _h(PROV_DOMAIN + "\x1f".join(
        f"{k}={r[k]}" for k in sorted(r)))


def attestation_mode() -> str:
    """`tee` only when a hardware quote is actually configured; else `software`.
    We never claim a runtime guarantee we cannot back."""
    return "tee" if os.environ.get("GROUNDCHECK_TEE_QUOTE", "").strip() else "software"


def build_provenance(response: dict) -> dict:
    """The provenance object attached to a response: the evidence-chain root and
    leaves, the route, and the honest attestation mode. The root and route_hash
    are ALSO folded into the signed manifest (attest.py), so the Ed25519
    signature covers them — this object is the human/holder-readable view of
    what the signature binds."""
    chain = evidence_chain(response.get("sources", []))
    mode = attestation_mode()
    out = {
        "evidence_root": chain["root"],
        "evidence_chain": chain,
        "route": route_descriptor(response),
        "route_hash": route_hash(response),
        "attestation_mode": mode,
        "binds": "evidence content + stances + order + model route",
        "domain": PROV_DOMAIN,
    }
    if mode == "tee":
        out["tee_quote"] = os.environ["GROUNDCHECK_TEE_QUOTE"].strip()
    return out


def recompute_evidence_root(response: dict) -> str:
    """A third party's recomputation from the response alone — the check that
    makes the binding meaningful: this must equal provenance.evidence_root."""
    return evidence_chain(response.get("sources", []))["root"]
