# Groundcheck

![Groundcheck — verify a factual claim against live sources, over MCP](assets/og-card.png)

**The grounding check agents run before they commit to an answer.**

Groundcheck is an [MCP](https://modelcontextprotocol.io) server that verifies a factual
claim against live web sources and returns a **verdict**, a **confidence score**, and
**citations**. Any agent — Claude Code, Cursor, your own — can call it mid-task, before it
states a fact it isn't sure of.

```
claude mcp add groundcheck -- npx -y groundcheck
```

## Why

LLM agents assert facts they haven't checked. Groundcheck gives them a single, cheap call
to make before they do — turning "I think it's X" into "X (supported, 3 sources)" or
"unverified, don't claim it."

## Tools

| Tool | Use it when | Returns |
|------|-------------|---------|
| `verify_claim(claim, maxSources?)` | About to assert a fact you're unsure of | `{ verdict, confidence, rationale, sources }` |
| `check_citations(text, maxClaims?)` | Before publishing an AI-generated draft | per-claim verdict report |
| `attribution_badge()` | Want to mark content as checked | a Markdown badge |

`verdict` is one of `supported` · `refuted` · `unverified`.

## Setup

```bash
npm install
npm start
```

Out of the box it uses two layers:

- **Retrieval — Wikipedia** (keyless, works immediately). Override with your own search
  endpoint via `GROUNDCHECK_SEARCH_URL` (must return `{ results: [{title,url,snippet,stance?}] }`),
  or disable entirely with `GROUNDCHECK_SEARCH_BACKEND=stub`.
- **Stance classification — [free-llm-router](https://github.com/beepboop2025/free-llm-router)**.
  Set **one** free fast-tier provider key and stance classification runs for free. Fastest to
  get: a **Groq** key (free, ~2 min, 14,400 req/day):

  ```bash
  export GROQ_API_KEY="gsk_..."
  ```

  Point at a router checkout with `GROUNDCHECK_ROUTER_PATH` if it isn't at the default
  sibling path. With **no** provider key, stance stays unset and every verdict is
  `unverified` — the tool degrades honestly, it never guesses.

> Note: OpenRouter's `:free` models are quota-throttled (HTTP 429) and make a poor sole
> provider. Prefer Groq or Cerebras for the fast classification tier.

### Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `GROUNDCHECK_SEARCH_BACKEND` | _(unset)_ | `stub` to disable real retrieval |
| `GROUNDCHECK_SEARCH_URL` | Wikipedia | custom JSON search endpoint |
| `GROUNDCHECK_SEARCH_KEY` | — | bearer token for the custom endpoint |
| `GROUNDCHECK_ROUTER_PATH` | sibling checkout | path to `free-llm-router` Node port |
| `GROUNDCHECK_REPO_URL` | placeholder | URL used in the attribution footer/badge |
| `GROQ_API_KEY` _(or any router provider key)_ | — | enables stance classification |

## Architecture

```
src/server.js       MCP wiring (stdio). stdout = protocol, stderr = logs.
src/search.js       SearchProvider — live endpoint or honest stub.
src/verdict.js      computeVerdict — the aggregation rule (verdict + confidence). ← the brain.
src/attribution.js  the "Verified with Groundcheck" footer + badge.
```

The interesting logic lives in `src/verdict.js`: how much source agreement counts as
"supported," how to treat conflicting sources, and how confidence scales. Tune it there.

## Contributing / extending

- **Better claim extraction:** `extractClaims` in `server.js` is a naive sentence splitter — swap in an LLM that pulls only atomic, checkable claims.
- **Stance classification:** have your search backend label each result's stance.
- **Caching:** memoize `provider.search` by claim to cut repeated lookups.

MIT.
