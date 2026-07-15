# Delivery attestation: signed receipts for agentic commerce

`POST /attest-delivery` (MCP: `attest_delivery`) is Groundcheck acting as the
**neutral verification layer between an agent's payment and a service's
outcome**. An agent pays some other service over x402 and gets a response;
nothing in the x402 flow itself records whether what arrived matches what was
advertised. The field's surveys name this the payment–service accountability
gap (SoK: Blockchain Agent-to-Agent Payments, arXiv:2604.03733; A402,
arXiv:2603.01179; RAILS, arXiv:2606.08790). A delivery attestation closes it
after the fact: one signed, offline-verifiable receipt binding

1. **payment** — the x402 settlement receipt the buyer presented (bound by
   hash, decoded fields echoed: network, transaction, payer, success),
2. **delivery** — the exact response bytes (`response_sha256`), optionally the
   request (`request_sha256`), and structural conformance to the JSON schema
   the service advertised in its 402 offer or Bazaar listing,
3. **content** — grounded verdicts (supported / refuted / unverified, with
   confidence and cited sources) over the factual claims in the response.

## The delivery verdict

Consistency, never merit — "as advertised and not contradicted", not "good":

| verdict | meaning |
|---|---|
| `consistent` | nothing refuted, and a positive signal: supported claims and/or the advertised schema validates |
| `degraded` | a minority of claims refuted — the content partially fails |
| `inconsistent` | not what was advertised: schema invalid, or refuted claims match/outnumber supported ones |
| `unverifiable` | nothing contradicted, but nothing confirmable either |

A shape violation is judged before content: a response that is not even the
advertised shape cannot be redeemed by containing some true sentences.

## Calling it

```bash
curl -s -X POST https://<host>/attest-delivery -H 'content-type: application/json' -d '{
  "service": "https://api.vendor.xyz/enrich",
  "response_text": "{\"figi\": \"BBG000B9XRY4\", \"name\": \"APPLE INC\"}",
  "request_text": "resolve AAPL",
  "payment_receipt": "<the X-PAYMENT-RESPONSE header value you received>",
  "advertised_schema": {"type": "object", "required": ["figi", "name"]}
}'
```

`payment_receipt` accepts the raw base64 header value or its decoded JSON.
`advertised_schema` is the schema the service published; conformance checking
covers the structural subset those listings actually use: `type`, `required`,
`properties`, `items`, `enum` (unknown keywords are ignored, never failed on).

## What a delivery receipt proves

The receipt is the standard Groundcheck attestation (`kind: "delivery"`,
Ed25519 over a canonical manifest — mechanics and offline verification in
[attested-receipts.md](attested-receipts.md)). Specifically it proves that
Groundcheck, at `signed_at`:

- was shown **this exact response** (hash-bound) from **this service**, with
  **this settlement receipt** presented alongside it (hash-bound, decoded
  transaction fields echoed),
- found the response to conform / not conform to **this advertised schema**,
  with the listed problems,
- grounded **these claims** to **these verdicts** with **these confidences**,
- and concluded **this delivery verdict** for **these reasons**.

Change any of it later — the verdict, a claim's outcome, the bound
transaction hash — and the manifest hash no longer recomputes, so the
signature fails. A saved response JSON is a self-contained dispute artifact.

## What it does not prove

- **That the payment happened on-chain.** Binding records what receipt the
  buyer *presented*; the transaction hash is echoed precisely so a verifier
  can confirm it on-chain themselves. A fabricated settlement receipt binds
  just as cleanly — it only proves the buyer presented that receipt.
- **That the request produced this response.** Groundcheck was not in the
  TLS session; the buyer supplies the exchange. What is proven is what the
  buyer committed to at attestation time — which is exactly what a dispute
  needs both sides to have fixed in advance.
- **That the service is good**, or that unverified claims are false. Same
  epistemics as every Groundcheck verdict ([attested-receipts.md](attested-receipts.md)).

## The loop pricing

Delivery attestation is the bundle tier of the verification loop:

| step | endpoint | price |
|---|---|---|
| extract | `POST /extract` | $0.005 |
| ground | `POST /check` | $0.02 |
| attest delivery (bundle) | `POST /attest-delivery` | $0.05 |

An agent that only wants to see which claims a document makes pays half a
cent; one that wants the full payment-to-delivery accountability trail pays
five. All prices are operator dials (`GROUNDCHECK_X402_PRICE_*`, docs/x402.md).
