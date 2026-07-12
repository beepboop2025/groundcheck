---
title: "Stop your AI agent from asserting facts it never checked (one MCP call)"
published: false
tags: ai, mcp, llm, opensource
cover_image: https://raw.githubusercontent.com/beepboop2025/groundcheck/main/assets/og-card.png
canonical_url:
---

> Handle (`beepboop2025`) is filled in; the `cover_image` URL resolves once the repo is pushed
> to `main`. Keep `published: false` until you've proofed it, then flip to `true`.

LLM agents have a tell: they state facts they never checked. A version number, a release date,
a "the default timeout is 30s" — the model pattern-matches something plausible and commits to
it with full confidence. In a chat that's a shrug. In an agent that's writing code, docs, or a
report, it ships.

The usual fixes are blunt. "Say 'I don't know' when unsure" works until the model is confidently
wrong, which is exactly the failure case. Full RAG is heavy for what's often a single yes/no:
*is this specific claim true?*

So I built a small thing for that single question.

**Groundcheck — the grounding check agents run before they commit to an answer. An MCP server
that verifies a factual claim against live sources and returns a verdict, a confidence score,
and citations. Any AI agent can call it mid-task.**

## The one call

It's a [Model Context Protocol](https://modelcontextprotocol.io) server, so any MCP client
(Claude Code, Cursor, your own) can install it in one line:

```bash
claude mcp add groundcheck -- npx -y groundcheck-mcp
```

Now the agent has a `verify_claim` tool. Before it asserts something it isn't sure of, it calls:

```jsonc
verify_claim({ claim: "The Eiffel Tower is located in Paris, France." })
// →
{
  "claim": "The Eiffel Tower is located in Paris, France.",
  "verdict": "supported",
  "confidence": 0.91,
  "rationale": "3 source(s) support the claim, none disagree.",
  "sources": [ { "title": "Eiffel Tower", "url": "https://en.wikipedia.org/?curid=…", "stance": "supports" }, … ]
}
```

`verdict` is one of `supported`, `refuted`, or `unverified`. There's also `check_citations`,
which extracts every claim from a block of text and verifies each — point it at an
AI-generated draft before you publish it.

## How it works

Three swappable layers:

1. **Retrieval.** Gather sources for the claim. Default is Wikipedia (keyless, so it runs with
   zero setup); override with any search endpoint you like.
2. **Stance.** An LLM labels each source `supports` / `refutes` / `neutral` toward the claim.
   This runs through a free-tier OpenAI-compatible provider (Groq, Cerebras, your own), so the
   check costs nothing.
3. **Verdict.** A small, deterministic rule turns the stances into a verdict + confidence.

That last layer is where the judgment lives, and the decisions matter more than the code length
suggests. The one I'd defend hardest:

```js
// Conflict → refuse to call it, don't majority-vote.
if (supports > 0 && refutes > 0) {
  return { verdict: "unverified", confidence: 0.25,
           rationale: `Sources disagree (${supports} support, ${refutes} refute).` };
}
```

A fact-checker that confidently repeats a *contested* claim because three sources outnumber two
is doing the most damage exactly when it should be most careful. So conflict returns
`unverified`. Two more rules in the same spirit: a lone source is a lean, not a ruling
(confidence caps at 0.6 until a second source agrees), and confidence *saturates* — going from
two agreeing sources to three adds a little, not a lot, and never reaches 1.0.

## The part I care about most: it degrades honestly

The failure mode for a verification tool isn't being unavailable. It's lying when it's
misconfigured — returning a green "supported" because the backend quietly returned nothing. So
non-evidence is filtered before any verdict math: a disabled search backend, a missing LLM key,
or sources with no clear stance all flow toward `unverified`. An unconfigured Groundcheck
**cannot** return "supported." That's enforced by the data flow, not by a comment asking nicely.

## Honest limitations

- Default Wikipedia retrieval covers encyclopedic facts well and long-tail / very recent claims
  poorly. Swap in a real web-search endpoint for broader coverage.
- The stance step needs one free LLM key. (Heads up: OpenRouter's `:free` models are
  quota-throttled and return HTTP 429 a lot — a poor sole provider. Groq's free tier is the
  easy path.)
- It checks *factual* claims. It won't help with opinions, predictions, or anything not yet
  written down somewhere.

## Try it

```bash
claude mcp add groundcheck -- npx -y groundcheck-mcp
```

Repo, the full verdict logic, and an `llms.txt` / `SKILL.md` so your agent knows *when* to reach
for it: **https://github.com/beepboop2025/groundcheck**

If you're building agents: where do you put the anti-hallucination check — in the prompt, in a
retrieval step, or as a tool the agent calls on itself? I went with the last one, but I'm not
sure it's the right default.
