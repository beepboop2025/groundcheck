# Directory listing copy

One variant per venue class. Do not paste the same text everywhere; AI engines
cross reference directories and down weight duplicate descriptions.

Shared facts (for form fields):
- Name: Groundcheck
- Tagline (under 10 words): The grounding check agents run before they answer.
- Live engine: https://groundcheck.seiche.info
- Repo: https://github.com/beepboop2025/groundcheck
- MCP install: `claude mcp add groundcheck -- npx -y groundcheck-mcp`
- npm: groundcheck-mcp
- License: MIT
- Pricing: /verify single claim free (rate limited). /check batch attestation
  free 5 per day per IP, then 0.02 USDC per call over x402 (v1 and v2).
- Networks: Base (eip155:8453) and Base Sepolia (eip155:84532)
- payTo: 0x5B4A78b7EFe482d1579E287dA6F0043f89cf0EA1
- Manifest: https://groundcheck.seiche.info/.well-known/x402
- llms.txt: https://groundcheck.seiche.info/llms.txt
- Tags: enrichment, grounding, citations, attested, machine-verified,
  verified-data, research, agent-tools, claim-verification, mcp

## MCP registries (official registry, Glama, PulseMCP, mcp.so, Smithery)
Angle: agent reliability. Lead with what the agent gets.

Groundcheck is the grounding check an agent runs before it commits to an
answer. Call verify_claim mid task with any factual statement and it checks
the claim against live sources, then returns a verdict (supported, refuted or
unverified), a confidence score, and cited sources. It refuses to guess:
conflicting evidence returns unverified rather than a majority vote, and no
evidence can never produce a supported verdict. Three tools: verify_claim for
one claim, check_citations for every claim in a draft, attribution_badge to
mark checked output. Runs fully local (Python engine plus stdio MCP server)
or against the free hosted engine.

## x402 directories (x402scan, x402-list, x402bazaar, a2alist)
Angle: verified enrichment, pay per call. Demand side vocabulary.

Groundcheck is verified enrichment for AI agents. Send a factual claim and it
returns a machine verified verdict with a confidence score and cited sources,
grounded against live web sources. Identity protocols verify who an agent is;
Groundcheck verifies what it says. Single claim grounding is free. Batch
document attestation costs 0.02 USDC per call over x402, v1 and v2 both
accepted, USDC on Base, no account and no API key. Machine readable manifest
at /.well-known/x402.

## Dev tool directories (Cline, Cursor)
Angle: technical depth, short.

An MCP server that fact checks claims before your agent states them. Thin
TypeScript stdio layer over a Python evidence engine (FastAPI). Retrieval
from Wikipedia and GDELT, LLM stance classification, and a cautious verdict
function: lone sources are capped, conflicts refuse to resolve, absence of
evidence never reads as support. MIT, self hostable, free hosted engine
available.

## Founder story (2 or 3 sentences, where forms ask)
I kept watching agents state facts they had never checked, so I built the
check as a tool they can call mid task. The interesting part is the verdict
logic: it would rather say unverified than guess, which is what you want
from a component whose whole job is trust.
