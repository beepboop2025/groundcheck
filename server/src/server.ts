#!/usr/bin/env node
// Groundcheck — MCP server (thin protocol layer). stdout is the JSON-RPC channel;
// every human-facing message goes to stderr. All logic lives in the Python engine.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

import { ENGINE_URL, checkCitations, resolveInstrument, verifyClaim } from "./engine.js";
import { ensureEngine } from "./spawn.js";
import { attributionBadge, attributionFooter } from "./attribution.js";
import type { VerifyResult } from "./types.js";

const server = new McpServer({ name: "groundcheck", version: "0.4.0" });

// Honest degradation when the engine isn't running — never a fabricated verdict.
function engineDown(claim: string): VerifyResult {
  return {
    claim,
    verdict: "unverified",
    confidence: 0,
    rationale: `Engine unreachable at ${ENGINE_URL}. Start it with: docker compose up -d (or make engine).`,
    backend: "offline",
    classifier: "none",
    sources: [],
  };
}

server.tool(
  "verify_claim",
  "PURPOSE: Fact-check one claim against live sources and return a result you can GATE A " +
    "DECISION ON. Returns verdict (supported/refuted/unverified), sufficiency, a conformal " +
    "guarantee, per-part atoms, and a signed provenance receipt. " +
    "GUIDELINES: Call BEFORE asserting or acting on a fact you are unsure of. Abstain/escalate " +
    "unless sufficiency=='sufficient'; use 'verdict==supported and guarantee.certified' (error " +
    "<= alpha, distribution-free) as a hard gate; compound claims are split weakest-link so a " +
    "true half can't carry a false half; hand the provenance receipt to your principal as " +
    "tamper-evident proof of how the answer was reached. Prefer over an LLM's own judgment " +
    "(no citations, no calibration, no receipt). " +
    "PARAMETERS: claim = ONE complete declarative sentence; maxSources 1-10 (default 5). " +
    "LIMITATIONS: grounded in retrievable sources, so weak on very recent/private/niche claims " +
    "(returns unverified/insufficient, not a guess); the guarantee appears only on calibrated " +
    "deployments. EXAMPLE: verify_claim({claim:'The Eiffel Tower is in Paris.'}).",
  {
    claim: z.string().describe("The factual claim to verify, written as one complete sentence."),
    maxSources: z.number().int().min(1).max(10).default(5).optional(),
  },
  async ({ claim, maxSources = 5 }) => {
    let result: VerifyResult;
    try {
      result = await verifyClaim(claim, maxSources);
    } catch {
      result = engineDown(claim);
    }
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) + attributionFooter(result) }],
    };
  }
);

server.tool(
  "check_citations",
  "PURPOSE: Fact-check EVERY claim in a block of text and return a per-claim report — the " +
    "batch form of verify_claim, for AI-generated drafts before you publish or act on them. " +
    "GUIDELINES: each reported claim carries verdict, sufficiency (abstain/escalate on anything " +
    "but 'sufficient'), and a conformal guarantee when certified; the response is covered by a " +
    "signed receipt bound to a hash of your text, so you can prove which document was checked. " +
    "Use verify_claim for a single claim. PARAMETERS: text = the prose (claims extracted " +
    "automatically); maxClaims 1-20 (default 8). LIMITATIONS: skips questions/opinions, bounded " +
    "by maxClaims, same source limits as verify_claim.",
  {
    text: z.string().describe("Text whose factual claims should be checked."),
    maxClaims: z.number().int().min(1).max(20).default(8).optional(),
  },
  async ({ text, maxClaims = 8 }) => {
    try {
      const report = await checkCitations(text, maxClaims);
      return { content: [{ type: "text", text: JSON.stringify(report, null, 2) }] };
    } catch {
      const payload = { checked: 0, backend: "offline", report: [], error: `Engine unreachable at ${ENGINE_URL}` };
      return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
    }
  }
);

server.tool(
  "resolve_instrument",
  "PURPOSE: Resolve a security identifier (ticker, ISIN, CUSIP, SEDOL, FIGI) or name to " +
    "canonical FIGI records via Bloomberg open symbology (OpenFIGI), WITH provenance and a " +
    "signed receipt. GUIDELINES: call BEFORE acting on any claim, order, or document that names " +
    "a security, so you know exactly WHICH instrument it is (disambiguating colliding tickers) " +
    "and can prove the mapping to your principal; prefer an explicit identifier over a plain " +
    "name. PARAMETERS: query = ticker/ISIN/CUSIP/SEDOL/FIGI/name; idType optional (auto-detected); " +
    "maxResults 1-10 (default 5). LIMITATIONS: conservative — returns matched=false rather than " +
    "guessing on an ambiguous name; does not price instruments or resolve crypto tokens. " +
    "EXAMPLE: resolve_instrument({query:'US0378331005', idType:'ID_ISIN'}).",
  {
    query: z.string().min(1).max(200).describe("Ticker, ISIN, CUSIP, SEDOL, FIGI, or instrument name."),
    idType: z
      .enum(["TICKER", "ID_ISIN", "ID_CUSIP", "ID_SEDOL", "ID_BB_GLOBAL"])
      .optional()
      .describe("Identifier type; auto-detected from the value's shape when omitted."),
    maxResults: z.number().int().min(1).max(10).default(5).optional(),
  },
  async ({ query, idType, maxResults = 5 }) => {
    try {
      const result = await resolveInstrument(query, idType, maxResults);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    } catch (err) {
      const payload = {
        query,
        matched: false,
        instruments: [],
        error: err instanceof Error ? err.message : `Engine unreachable at ${ENGINE_URL}`,
      };
      return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
    }
  }
);

server.tool(
  "attribution_badge",
  "Return a Markdown badge to embed in a README or report, signalling the content was checked with Groundcheck.",
  {},
  async () => ({ content: [{ type: "text", text: attributionBadge() }] })
);

// Make sure an engine is available before exposing tools — reuse a running one,
// otherwise auto-spawn it. Never throws; tools degrade honestly if it's absent.
const engine = await ensureEngine();
const ENGINE_STATUS: Record<typeof engine.status, string> = {
  reachable: `engine reachable ✓ (${ENGINE_URL})`,
  spawned: `engine auto-started ✓ — ${engine.detail}`,
  disabled: `engine auto-spawn disabled (GROUNDCHECK_NO_SPAWN); expecting one at ${ENGINE_URL}`,
  "not-found": `engine UNREACHABLE — ${engine.detail}`,
  failed: `engine UNREACHABLE — ${engine.detail}`,
};

// Exit when the client goes away (stdin EOF / transport close) so the process
// `exit` handler stops any engine we auto-spawned — otherwise it would orphan.
// Listen on both the protocol close and stdin directly (belt and suspenders).
const shutdown = () => process.exit(0);
server.server.onclose = shutdown;
process.stdin.once("end", shutdown);
process.stdin.once("close", shutdown);

const transport = new StdioServerTransport();
await server.connect(transport);
console.error(`groundcheck MCP server up — ${ENGINE_STATUS[engine.status]}`);
