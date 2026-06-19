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

## How to use it
1. Phrase the claim as one complete, atomic sentence.
2. Call `verify_claim` with it.
3. If the verdict is `refuted` or `unverified`, do NOT assert the claim — correct it, hedge it, or cite the returned sources. Only state it plainly when `supported` with reasonable confidence.

## Setup
```
claude mcp add groundcheck -- npx -y groundcheck
```
Set `GROUNDCHECK_SEARCH_URL` (and optionally `GROUNDCHECK_SEARCH_KEY`) to a JSON search
endpoint. Without it, the server runs in clearly-labelled stub mode and returns
`unverified` — it will never fake a confident verdict.
