---
name: groundcheck
description: Verify factual claims against live web sources before asserting them. Use when about to state a fact you are not certain of (a date, number, name, statistic), or before publishing AI-generated text that makes factual claims. Returns a verdict (supported/refuted/unverified), a confidence score, and citations.
---

# Groundcheck

Use the `groundcheck` MCP server to fact-check before you commit to an answer.

## When to reach for it
- You are about to assert a date, number, name, or claim you are not fully certain of → `verify_claim`.
- You generated a draft (README, report, answer) that makes factual claims → `check_citations` on the draft.
- The user wants proof their content was checked → `attribution_badge`.
- Text names a security ($AAPL, an ISIN, "Reliance Industries bond") and you need to know exactly which instrument it is → `resolve_instrument`.

## How to use it
1. Phrase the claim as one complete, atomic sentence.
2. Call `verify_claim` with it.
3. If the verdict is `refuted` or `unverified`, do NOT assert the claim — correct it, hedge it, or cite the returned sources. Only state it plainly when `supported` with reasonable confidence.

## Setup
Two parts: a Python engine (does the work) and a TypeScript MCP server (the interface).
```
make install && export GROQ_API_KEY=... && make engine   # 1. start the engine on :8723
claude mcp add groundcheck -- npx -y groundcheck-mcp         # 2. register the MCP server
```
Retrieval defaults to Wikipedia (keyless). With no provider key, the engine still runs but
every verdict is `unverified` — it will never fake a confident verdict.
