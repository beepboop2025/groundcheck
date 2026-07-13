# Attested receipts

Every `/verify` and `/check` response from the engine carries an `attestation`
field: an Ed25519 signature over a fixed subset of the response. An agent that
pays for a verdict can hand its principal the response JSON, and the principal
can check, offline and without trusting the agent or calling Groundcheck again,
that Groundcheck really returned that verdict at that time.

## What a receipt proves

- Groundcheck (the holder of the signing key) produced this exact verdict, with
  this confidence, over this input, citing these source URLs, at the time in
  `receipt.signed_at`.
- The response was not altered afterwards. Change the verdict, the confidence,
  a source URL, or the claim itself and the manifest hash no longer matches, so
  verification fails.
- For `/check`, the receipt is also bound to a hash of the submitted text, so
  the buyer can prove which document the report was about.

## What a receipt does not prove

- That the verdict is correct. The signature covers what Groundcheck said, not
  whether Groundcheck was right. A confidently wrong verdict verifies just as
  cleanly as a correct one.
- That the cited sources say what the stance classifier thought they said, or
  that they still exist.
- The wall clock. `signed_at` is our server's claim about the time. There is no
  external timestamp authority behind it, so a receipt cannot prove it was not
  backdated by the key holder. It does prove the time claim came from the key
  holder and was not edited later by anyone else.
- Anything about responses signed with an ephemeral key beyond process
  identity. See key modes below.

## How to verify offline

The receipt lives at `response.attestation.receipt`:

```json
{
  "kind": "verify",
  "manifest_hash": "…64 hex…",
  "sig": "…128 hex…",
  "public_key": "…64 hex…",
  "algo": "ed25519",
  "domain": "groundcheck-attest-v1",
  "signed_at": "2026-07-13T09:00:00+00:00"
}
```

Three steps, any language, any Ed25519 library:

1. Rebuild the manifest from the response. For `kind: "verify"` it is:

   ```json
   {
     "backend": response.backend,
     "claim_sha256": sha256 hex of response.claim (utf-8),
     "confidence": response.confidence,
     "model": response.classifier,
     "signed_at": receipt.signed_at,
     "source_urls": [each response.sources[i].url, in order],
     "verdict": response.verdict
   }
   ```

   For `kind: "check"`: `backend`, `checked`, `claims` (a list of
   `{claim_sha256, verdict, confidence}` built from `response.report` in
   order), `input_sha256` (from `attestation.input_sha256`, or recompute it
   from the original text you submitted), `model`, `signed_at`.

2. Hash it canonically: `sha256` of the JSON serialization with sorted keys and
   compact separators (in Python:
   `json.dumps(m, sort_keys=True, separators=(",", ":"))`). The result must
   equal `receipt.manifest_hash`.

3. Verify `receipt.sig` over the message
   `groundcheck-attest-v1:<kind>:<manifest_hash>` using `receipt.public_key`.
   The domain prefix means a Groundcheck receipt can never be replayed as a
   signature from some other system, and a `verify` receipt can never pose as a
   `check` receipt.

A worked ten line Python example is served by the engine itself at
`GET /attest/pubkey` (field `verify_example`), alongside the current public key
and key mode.

## Key modes and rotation

The signing key comes from the `GROUNDCHECK_ATTEST_KEY` environment variable
(a 64 character hex Ed25519 seed; generate one with
`python -m groundcheck_engine.attest generate-key`). Environment only, no key
files: the hosted engine may run on an ephemeral filesystem.

- **persistent**: the env var is set. All receipts trace to one operator
  identity, published at `/attest/pubkey`.
- **ephemeral**: the env var is not set. The engine still signs, using a key
  generated at process start, and logs a warning. Receipts remain internally
  verifiable against the public key inside each receipt, but the identity dies
  with the process, so an ephemeral receipt cannot be tied to the Groundcheck
  operator. `/attest/pubkey` reports which mode is live; check it if the
  operator identity matters to you.

Rotation: old receipts verify against the public key recorded inside them, so
rotating the seed never invalidates history. What rotation does change is which
key `/attest/pubkey` currently vouches for, so operators should announce old
public keys when they rotate, and verifiers who care about operator identity
should pin the pubkey they trust rather than fetching it at verification time.

Losing the seed means future receipts sign under a new identity; it does not
break old ones. Leaking the seed means anyone can mint receipts in your name
until you rotate, so treat it like any other private key.

## The x402 angle

Groundcheck's `/check` endpoint is machine payable over x402 (see
`docs/x402.md`). Attestation is what makes that purchase auditable: the agent
pays a few cents, gets a verdict report, and the receipt in the same response
is durable proof of exactly what was bought, over which input, from whom, and
when. The principal who funded the wallet does not have to trust the agent's
summary of what Groundcheck said, and a dispute about a verdict months later
can be settled from the saved response JSON alone.
