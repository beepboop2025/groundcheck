# groundcheck-mcp

The grounding check agents run before they commit to an answer.

Groundcheck is an MCP server that verifies a factual claim against live
sources and returns a verdict (supported, refuted or unverified), a
confidence score, and cited sources. Call it mid task, before your agent
states a fact it is not sure of. It refuses to guess: conflicting evidence
returns unverified, and no evidence can never produce a supported verdict.

## Install

```bash
claude mcp add groundcheck -- npx -y groundcheck-mcp
```

Works the same way in Cursor, Cline, VS Code, or any MCP client that speaks
stdio.

## Tools

| Tool | What it does |
| --- | --- |
| `verify_claim` | Ground one factual claim. Returns verdict, confidence, sources, sufficiency, conformal guarantee, signed receipt. |
| `check_citations` | Extract and verify every claim in a draft. Per claim report. |
| `resolve_instrument` | Resolve a ticker/ISIN/CUSIP/SEDOL/FIGI or name to canonical FIGI identity with provenance. |
| `extract_claims` | Split text into independently checkable atomic claims (rule-based, receipt-bound). |
| `attest_delivery` | Signed, offline-verifiable delivery receipt binding an x402 payment to what the paid service delivered. |
| `attribution_badge` | Markdown badge marking content as groundchecked. |

## Engine

This package is a thin stdio layer. The evidence engine (retrieval, stance
classification, verdict) is a Python service. By default the server looks
for it at `http://127.0.0.1:8723` and can spawn it from a local clone; set
`GROUNDCHECK_ENGINE_URL=https://groundcheck.seiche.info` to use the hosted
engine instead. On the hosted engine `verify_claim` is free forever; the
other verification tools are pay-per-call (x402, USDC on Base) after a small
free daily trial — a 402 response arrives as a readable tool result with the
offer, never an opaque error. A local engine is free and unmetered.

Source, engine code, and self hosting guide:
https://github.com/beepboop2025/groundcheck

## License

MIT
