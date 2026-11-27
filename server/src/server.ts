#!/usr/bin/env node
// Groundcheck — MCP server (thin protocol layer). stdout is the JSON-RPC channel;
// every human-facing message goes to stderr. All logic lives in the Python engine.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

import {
  ENGINE_URL,
  PaymentRequiredError,
  attestDelivery,
  checkCitations,
  extractClaims,
  resolveInstrument,
  verifyClaim,
} from "./engine.js";
import { ensureEngine } from "./spawn.js";
import { attributionBadge, attributionFooter } from "./attribution.js";
import type { VerifyResult } from "./types.js";

const server = new McpServer({ name: "groundcheck", version: "0.6.0" });

// A 402 from a hosted engine is a payment offer, not a failure: return it as a
// readable isError result (with the raw offer in structuredContent) so the
// agent can pay and retry — a thrown error would surface as an opaque protocol
// fault the agent cannot act on.
function paymentRequired(err: PaymentRequiredError) {
  return {
    content: [{ type: "text" as const, text: err.message }],
    structuredContent: err.offer ?? undefined,
    isError: true,
  };
}

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
  "Fact-check one claim against live retrieved sources. Opt-in AI service: a language " +
    "model labels each source's stance. Returns verdict (supported, refuted or unverified), " +
    "sufficiency, a conformal guarantee, per-part atoms and a signed provenance receipt " +
    "binding the evidence and model route. Sufficiency other than sufficient (insufficient, " +
    "no_sources, no_stance, conflict) is a reason to abstain or escalate. When " +
    "guarantee.certified is true, the error probability is calibrated to at most " +
    "guarantee.alpha, so supported plus certified is the strongest signal; a person or a " +
    "rule decides. Compound claims are verified part by part and recombined weakest-link. " +
    "Limits: evidence is web, encyclopedic and news sources, so very recent, private, niche " +
    "or opinion claims return unverified or insufficient. The guarantee exists only on calibrated " +
    "deployments, for claims exchangeable with the calibration set, which holds no finance " +
    "figures. It checks source support, not ultimate truth. Free.",
  {
    claim: z.string().max(10_000).describe("The factual claim to verify, written as one complete sentence."),
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
  "Fact-check every factual claim in a block of text, such as an AI-generated draft or " +
    "document before it is published or acted on, and return a per-claim report. Opt-in AI " +
    "service: a language model labels each source's stance. Claims are extracted " +
    "automatically. Each reported claim carries a verdict, sufficiency (anything but " +
    "sufficient is a reason to abstain or escalate) and a conformal guarantee when " +
    "certified. The calibration set holds no finance figures. The response is covered by a " +
    "signed receipt bound to a hash of the submitted text, so it can later prove which " +
    "document was checked. Limits: only " +
    "declarative factual sentences are checked (questions and opinions are skipped), at " +
    "most maxClaims of them, with the same source-coverage limits as single-claim " +
    "verification. Paid per call on the hosted engine (x402); free on a local engine.",
  {
    text: z.string().max(100_000).describe("Text whose factual claims should be checked."),
    maxClaims: z.number().int().min(1).max(20).default(8).optional(),
  },
  async ({ text, maxClaims = 8 }) => {
    try {
      const report = await checkCitations(text, maxClaims);
      return { content: [{ type: "text", text: JSON.stringify(report, null, 2) }] };
    } catch (err) {
      if (err instanceof PaymentRequiredError) return paymentRequired(err);
      const payload = { checked: 0, backend: "offline", report: [], error: `Engine unreachable at ${ENGINE_URL}` };
      return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
    }
  }
);

server.tool(
  "resolve_instrument",
  "Resolve a security identifier (ticker, ISIN, CUSIP, SEDOL or FIGI) or an instrument " +
    "name to canonical FIGI records via OpenFIGI, Bloomberg's open symbology, with " +
    "provenance and a signed receipt. Rule-based, no language model. Useful whenever a " +
    "claim, order or document names a " +
    "security: it shows exactly which instrument is meant, disambiguating colliding " +
    "tickers, and the receipt proves the mapping. An explicit identifier resolves more " +
    "reliably than a plain name; idType is auto-detected when omitted. Limits: conservative " +
    "by design, it returns matched=false rather than guessing on an ambiguous name, and it " +
    "does not price instruments or resolve crypto tokens. Paid per call on the hosted " +
    "engine (x402); free on a local engine.",
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
      if (err instanceof PaymentRequiredError) return paymentRequired(err);
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
  "extract_claims",
  "Split text into independently checkable atomic factual claims: the low-cost first step " +
    "of a verification loop (extract, ground, attest). Returns claims, count and " +
    "input_sha256, plus a signed receipt bound to the input hash. Useful to see which " +
    "claims a document makes before paying to ground them, to budget a verification pass by " +
    "grounding only the claims that matter, or to prove later exactly which claims came " +
    "from which text. Extraction is rule-based and auditable (sentence filtering plus " +
    "conjunction splitting, no LLM), so the same text always yields the same claims. " +
    "Limits: it skips questions, opinions, instructions and first-person statements, and " +
    "splits only on high-precision conjunction boundaries, so under-splitting is possible. " +
    "It verifies nothing. Paid per call on the hosted engine (x402, at its lowest price); " +
    "free on a local engine.",
  {
    text: z.string().min(1).max(100_000).describe("The text to split into independently checkable atomic factual claims."),
    maxClaims: z.number().int().min(1).max(50).default(20).optional()
      .describe("Max claims to return (1-50)."),
  },
  async ({ text, maxClaims = 20 }) => {
    try {
      const result = await extractClaims(text, maxClaims);
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    } catch (err) {
      if (err instanceof PaymentRequiredError) return paymentRequired(err);
      const payload = { count: 0, claims: [], method: "offline", error: `Engine unreachable at ${ENGINE_URL}` };
      return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
    }
  }
);

server.tool(
  "attest_delivery",
  "Neutral delivery verification for agentic commerce: after an x402 payment to another " +
    "service, it checks what was delivered and returns a signed, offline-verifiable receipt " +
    "binding payment, delivery and content (settlement receipt hash and decoded transaction " +
    "fields, response sha256, structural conformance to the advertised schema, grounded " +
    "verdicts on the response's factual claims). The claim checks use a language model for " +
    "stance; the other checks are rule-based. delivery_verdict, with a rationale: " +
    "consistent (proceed), degraded (use with caution; refuted claims flagged), " +
    "inconsistent (not reliable; the receipt is dispute evidence) or unverifiable (nothing " +
    "contradicted or confirmed). Limits: it judges consistency with what was advertised, " +
    "never service quality. Payment binding records which receipt was presented; on-chain " +
    "confirmation is a separate step. Claim checks share the usual source-coverage limits. " +
    "Paid per call on the hosted engine (x402).",
  {
    service: z.string().min(1).max(500)
      .describe("URL (or name) of the paid service whose delivery is being verified."),
    responseText: z.string().min(1).max(100_000)
      .describe("The delivered payload, verbatim (JSON or prose)."),
    requestText: z.string().max(10_000).optional()
      .describe("What was asked of the service (optional; bound by hash when given)."),
    paymentReceipt: z.string().max(16_384).optional()
      .describe("x402 settlement receipt from the paid call (X-PAYMENT-RESPONSE / PAYMENT-RESPONSE value, base64 or raw JSON)."),
    advertisedSchema: z.record(z.unknown()).optional()
      .describe("JSON schema the service advertised for its output (from its 402 offer or Bazaar listing)."),
    maxClaims: z.number().int().min(1).max(20).default(8).optional()
      .describe("Max claims in the delivered content to ground (1-20)."),
  },
  async ({ service, responseText, requestText, paymentReceipt, advertisedSchema, maxClaims = 8 }) => {
    try {
      const result = await attestDelivery({
        service,
        responseText,
        requestText,
        paymentReceipt,
        advertisedSchema,
        maxClaims,
      });
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    } catch (err) {
      if (err instanceof PaymentRequiredError) return paymentRequired(err);
      const payload = {
        service,
        delivery_verdict: "unverifiable",
        rationale: `Engine unreachable at ${ENGINE_URL}`,
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
