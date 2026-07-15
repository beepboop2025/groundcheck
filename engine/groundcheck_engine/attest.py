"""Signed receipts for verification responses (the provable half of
"attested enrichment").

Every /verify and /check response carries an `attestation` field: an Ed25519
signature over a deterministic manifest of what Groundcheck said (input hash,
verdict, confidence, source URLs, model id, timestamp). An agent that pays
for a verdict can hand its principal cryptographic proof of what Groundcheck
returned and when — verifiable offline with any Ed25519 library, no call back
to us. What a receipt proves and does not prove: docs/attested-receipts.md.

Signing patterns (domain separation, canonical JSON hashing, Ed25519 via the
`cryptography` package) are vendored from LiquiLens's attestation layer —
deliberately no cross-repo import.

Key handling is env-only because the production filesystem may be ephemeral
(Vercel):

  GROUNDCHECK_ATTEST_KEY   64-char hex Ed25519 seed. Set it and every receipt
                           is signed by the operator's persistent identity.
                           Unset: an ephemeral key is generated per process
                           and a warning is logged — receipts stay verifiable
                           against the pubkey in each receipt, but the signing
                           identity does not survive a restart. GET
                           /attest/pubkey reports which mode is live.

Generate an operator key:
    python -m groundcheck_engine.attest generate-key
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

logger = logging.getLogger("groundcheck.attest")

DOMAIN = "groundcheck-attest-v1"
ALGO = "ed25519"
ENV_KEY = "GROUNDCHECK_ATTEST_KEY"

# (env seed value, private key, public key hex) — cached per env value so a
# changed/cleared env var (tests, restarts-with-config) is picked up, while
# the ephemeral fallback stays stable for the life of the process.
_key_cache: Optional[Tuple[str, Any, str]] = None
_ephemeral_warned = False


# ---------------------------------------------------------------------------
# Canonical hashing (deterministic across processes and serializations)
# ---------------------------------------------------------------------------
def _jsonable(obj: Any) -> Any:
    """Stable fallback for non-JSON values in hashed manifests. Deterministic
    for the same input is all a content hash needs."""
    for attr in ("tolist", "item"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    return repr(obj)


def canonical_hash(obj: dict) -> str:
    """SHA-256 of the canonical JSON form: sorted keys, compact separators."""
    body = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_jsonable)
    return hashlib.sha256(body.encode()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _message(kind: str, manifest_hash: str) -> bytes:
    """Domain-separated signing message: a Groundcheck receipt signature can
    never be replayed as some other system's, or as another kind's."""
    return f"{DOMAIN}:{kind}:{manifest_hash}".encode()


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
def _load_key():
    """(private key, public key hex, mode). Env seed when set; else a
    process-lifetime ephemeral key. An env value that is set but malformed
    raises — silently signing under a different identity than the operator
    configured would be worse than not signing."""
    global _key_cache, _ephemeral_warned
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed_hex = os.environ.get(ENV_KEY, "").strip()
    if _key_cache is not None and _key_cache[0] == seed_hex:
        _, private, pub_hex = _key_cache
        return private, pub_hex, "persistent" if seed_hex else "ephemeral"

    if seed_hex:
        try:
            seed = bytes.fromhex(seed_hex)
        except ValueError:
            seed = b""
        if len(seed) != 32:
            raise ValueError(
                f"{ENV_KEY} must be a 64-char hex Ed25519 seed "
                "(generate one: python -m groundcheck_engine.attest generate-key)")
        private = Ed25519PrivateKey.from_private_bytes(seed)
        mode = "persistent"
    else:
        private = Ed25519PrivateKey.generate()
        mode = "ephemeral"
        if not _ephemeral_warned:
            _ephemeral_warned = True
            logger.warning(
                "attest: %s is not set — receipts are signed with an EPHEMERAL "
                "key that dies with this process. Receipts remain verifiable "
                "against the public key inside each receipt, but the signing "
                "identity will change on restart. Set a persistent key: "
                "python -m groundcheck_engine.attest generate-key", ENV_KEY)
    pub_hex = private.public_key().public_bytes_raw().hex()
    _key_cache = (seed_hex, private, pub_hex)
    return private, pub_hex, mode


def key_mode() -> str:
    """"persistent" (env seed) or "ephemeral" (generated this process)."""
    return _load_key()[2]


def public_key_hex() -> str:
    return _load_key()[1]


def generate_key() -> str:
    """A fresh Ed25519 seed for the operator to put in GROUNDCHECK_ATTEST_KEY."""
    return os.urandom(32).hex()


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------
def sign_receipt(kind: str, manifest: dict) -> dict:
    """Sign a manifest and return the portable receipt. signed_at echoes the
    manifest's own timestamp when present, so the time in the receipt is the
    time the signature actually covers."""
    private, pub_hex, _mode = _load_key()
    manifest_hash = canonical_hash(manifest)
    signed_at = manifest.get("signed_at") or _now()
    return {
        "kind": kind,
        "manifest_hash": manifest_hash,
        "sig": private.sign(_message(kind, manifest_hash)).hex(),
        "public_key": pub_hex,
        "algo": ALGO,
        "domain": DOMAIN,
        "signed_at": signed_at,
    }


def verify_receipt(kind: str, manifest: dict, receipt: dict) -> dict:
    """Independent verification: the manifest hash recomputes and the signature
    is valid under the public key recorded in the receipt. Pure function —
    third parties can vendor this file (or just these 15 lines) and run it
    offline. Reports, never raises."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    problems: List[str] = []
    if canonical_hash(manifest) != receipt.get("manifest_hash"):
        problems.append("manifest hash does not recompute (manifest was modified)")
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(receipt["public_key"]))
        pub.verify(bytes.fromhex(receipt["sig"]),
                   _message(kind, receipt["manifest_hash"]))
    except (InvalidSignature, ValueError, KeyError, TypeError):
        problems.append("signature INVALID")
    return {"valid": not problems, "problems": problems}


# ---------------------------------------------------------------------------
# Manifests — the deterministic subset of a response that gets signed.
# Everything here is either in the response itself or in the receipt
# (signed_at), so a third party can rebuild the manifest byte-for-byte from
# the response JSON alone. Never sign fields that vary per-serialization.
# ---------------------------------------------------------------------------
def build_verify_manifest(response: dict, signed_at: str) -> dict:
    """Manifest for one /verify (or MCP verify_claim) response.

    Beyond the verdict, the signature binds the EVIDENCE PATH: `evidence_root`
    is a rolling commitment over the ordered evidence content + stances
    (provenance.py), and `route_hash` binds the model route. Both are
    recomputable from the response, so a third party rebuilds this manifest
    byte-for-byte — and a swapped source, flipped stance, or silent model change
    breaks verification. Older responses without a provenance field fall back to
    the empty-evidence root, so verification stays consistent."""
    from . import provenance
    return {
        "claim_sha256": sha256_text(response["claim"]),
        "verdict": response["verdict"],
        "confidence": response["confidence"],
        "source_urls": [s["url"] for s in response.get("sources", [])],
        "evidence_root": provenance.recompute_evidence_root(response),
        "route_hash": provenance.route_hash(response),
        "model": response.get("classifier", ""),
        "backend": response.get("backend", ""),
        "signed_at": signed_at,
    }


def build_check_manifest(response: dict, signed_at: str, input_sha256: str) -> dict:
    """Manifest for one /check (or MCP check_citations) response. The input
    text is not echoed in the response, so its hash rides in the attestation
    (and anyone holding the original text can recompute it)."""
    return {
        "input_sha256": input_sha256,
        "checked": response["checked"],
        "claims": [
            {"claim_sha256": sha256_text(r["claim"]),
             "verdict": r["verdict"],
             "confidence": r["confidence"]}
            for r in response.get("report", [])
        ],
        "model": response.get("classifier", ""),
        "backend": response.get("backend", ""),
        "signed_at": signed_at,
    }


_NOTE = ("Ed25519 receipt over a deterministic manifest of this response. "
         "Verify offline: GET /attest/pubkey for the message format and a "
         "worked example, or docs/attested-receipts.md in the repo.")


def _attestation(kind: str, manifest: dict, extra: dict | None = None) -> dict:
    receipt = sign_receipt(kind, manifest)
    out = {"attested": True, "receipt": receipt,
           "manifest_keys": sorted(manifest), "note": _NOTE}
    if extra:
        out.update(extra)
    return out


def attest_verify_response(response: dict) -> dict:
    """Attestation field for a /verify response. Never raises — a signing
    failure must not break the endpoint."""
    try:
        return _attestation("verify", build_verify_manifest(response, _now()))
    except Exception as exc:
        logger.warning("attest: could not sign verify response: %s", exc)
        return {"attested": False, "reason": f"{type(exc).__name__}: {exc}"}


def attest_check_response(response: dict, input_text: str) -> dict:
    """Attestation field for a /check response. Never raises."""
    try:
        input_sha256 = sha256_text(input_text)
        manifest = build_check_manifest(response, _now(), input_sha256)
        return _attestation("check", manifest, {"input_sha256": input_sha256})
    except Exception as exc:
        logger.warning("attest: could not sign check response: %s", exc)
        return {"attested": False, "reason": f"{type(exc).__name__}: {exc}"}


def verify_attested_response(kind: str, response: dict) -> dict:
    """Convenience for holders of a full response JSON: rebuild the manifest
    from the response + receipt and verify. Pure, offline."""
    att = response.get("attestation") or {}
    if not att.get("attested"):
        return {"valid": False,
                "problems": [f"response is not attested "
                             f"({att.get('reason', 'no attestation field')})"]}
    receipt = att["receipt"]
    signed_at = receipt.get("signed_at", "")
    if kind == "verify":
        manifest = build_verify_manifest(response, signed_at)
    elif kind == "check":
        manifest = build_check_manifest(response, signed_at,
                                        att.get("input_sha256", ""))
    else:
        return {"valid": False, "problems": [f"unknown kind {kind!r}"]}
    return verify_receipt(kind, manifest, receipt)


# What GET /attest/pubkey publishes, kept next to the code it describes.
MESSAGE_FORMAT = f"{DOMAIN}:<kind>:<manifest_hash>"
CANONICALIZATION = ("manifest_hash = sha256 of json.dumps(manifest, "
                    "sort_keys=True, separators=(',', ':'))")
VERIFY_EXAMPLE = """import hashlib, json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
resp = json.load(open("verify_response.json"))  # the saved /verify response
r = resp["attestation"]["receipt"]
PD = "groundcheck-provenance-v1"
def H(s): return hashlib.sha256(s.encode()).hexdigest()
# evidence_root: rolling commitment over the ordered non-stub evidence
roll = H(PD)
for i, s in enumerate(x for x in resp["sources"] if not x.get("stub")):
    leaf = H(f"{i}\\x1f{s.get('url','')}\\x1f{s.get('snippet','')}\\x1f{s.get('stance') or 'none'}")
    roll = H(roll + leaf)
route = {"model": resp.get("classifier",""), "backend": resp.get("backend",""),
         "ensembled": str(resp.get("classifier","")).startswith("ensemble:"),
         "decomposed": bool(resp.get("atoms")), "n_atoms": len(resp.get("atoms") or []),
         "certified": bool((resp.get("guarantee") or {}).get("certified"))}
route_hash = H(PD + "\\x1f".join(f"{k}={route[k]}" for k in sorted(route)))
m = {"claim_sha256": H(resp["claim"]), "verdict": resp["verdict"],
     "confidence": resp["confidence"], "source_urls": [s["url"] for s in resp["sources"]],
     "evidence_root": roll, "route_hash": route_hash,
     "model": resp["classifier"], "backend": resp["backend"], "signed_at": r["signed_at"]}
h = hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
assert h == r["manifest_hash"], "manifest was modified"
assert roll == resp["provenance"]["evidence_root"], "evidence path was tampered"
Ed25519PublicKey.from_public_bytes(bytes.fromhex(r["public_key"])).verify(
    bytes.fromhex(r["sig"]), f"groundcheck-attest-v1:verify:{h}".encode())"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m groundcheck_engine.attest",
        description="Operator utilities for Groundcheck response attestation.")
    ap.add_argument("command", choices=["generate-key", "pubkey"])
    args = ap.parse_args()
    if args.command == "generate-key":
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        seed = generate_key()
        pub = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed)) \
            .public_key().public_bytes_raw().hex()
        print(json.dumps({
            "GROUNDCHECK_ATTEST_KEY": seed,
            "public_key": pub,
            "note": "Keep the seed secret (env var / secret store); "
                    "publish the public key.",
        }, indent=1))
        return
    if args.command == "pubkey":
        print(json.dumps({"public_key": public_key_hex(), "key_mode": key_mode(),
                          "algo": ALGO, "domain": DOMAIN}, indent=1))


if __name__ == "__main__":
    _cli()
