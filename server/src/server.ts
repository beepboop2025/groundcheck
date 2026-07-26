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
    } catch (err) {
      if (err instanceof PaymentRequiredError) return paymentRequired(err);
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
  "PURPOSE: Split text into independently checkable ATOMIC factual claims — the cheap first " +
    "step of a verification loop (extract -> ground -> attest). Returns {claims, count, " +
    "input_sha256} plus a signed receipt bound to the input hash. " +
    "GUIDELINES: call when you want to see WHICH claims a document makes before paying to " +
    "ground them, to budget a verification pass (extract everything, then verify_claim only " +
    "the claims that matter to your decision), or to prove later exactly which claims were " +
    "pulled from exactly which text (the receipt binds both). Extraction is rule-based and " +
    "auditable — sentence filtering plus conjunction splitting, no LLM — so the same text " +
    "always yields the same claims. Use check_citations when you want extraction AND grounding " +
    "in one call. PARAMETERS: text = the prose to decompose; maxClaims 1-50 (default 20). " +
    "LIMITATIONS: extracts declarative factual sentences; skips questions, opinions, " +
    "instructions, first-person statements; splits only on high-precision conjunction " +
    "boundaries so under-splitting is possible. Does NOT verify anything. Paid per call on the " +
    "hosted engine (x402, cheapest tool); free on a local engine. " +
    "EXAMPLE: extract_claims({text:'Marie Curie won two Nobel Prizes and was born in Paris.'}) " +
    "-> {count: 2, claims: ['Marie Curie won two Nobel Prizes', 'was born in Paris.']}.",
  {
    text: z.string().min(1).describe("The text to split into independently checkable atomic factual claims."),
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
  "PURPOSE: Neutral delivery verification for agentic commerce. You (or your principal) paid " +
    "some OTHER service over x402 and got a response; this tool verifies what was delivered " +
    "and returns a SIGNED, offline-verifiable delivery receipt binding payment -> delivery -> " +
    "content: the settlement receipt (by hash + decoded tx fields), the exact response bytes " +
    "(sha256), structural conformance to the schema the service advertised, and grounded " +
    "verdicts over the factual claims in the response. Returns delivery_verdict " +
    "(consistent | degraded | inconsistent | unverifiable) with a rationale. " +
    "GUIDELINES: call AFTER a paid third-party call whose output you will act on or account " +
    "for. Branch on delivery_verdict: consistent -> proceed; degraded -> use with caution, " +
    "flag the refuted claims; inconsistent -> do not rely on the delivery, keep the receipt as " +
    "dispute evidence; unverifiable -> nothing contradicted but nothing confirmed. Save the " +
    "full response JSON — it is a self-contained dispute artifact verifiable offline months " +
    "later (GET /attest/pubkey on the engine documents how). " +
    "PARAMETERS: service = URL/name of the paid service; responseText = the delivered payload " +
    "verbatim; requestText (optional) = what was asked; paymentReceipt (optional) = the " +
    "X-PAYMENT-RESPONSE value from the paid call; advertisedSchema (optional) = the JSON " +
    "schema the service advertised; maxClaims 1-20 (default 8). " +
    "LIMITATIONS: judges CONSISTENCY (as-advertised, not contradicted), never service " +
    "quality. Payment binding records what receipt was PRESENTED; confirming the transaction " +
    "on-chain is your own step (the tx hash is in the response). Schema conformance is " +
    "structural. Content checking has the same source-coverage limits as verify_claim. Paid " +
    "per call on the hosted engine (x402). " +
    "EXAMPLE: attest_delivery({service:'https://api.vendor.xyz/enrich', responseText:'{\"name\": " +
    "\"APPLE INC\"}', paymentReceipt:'<X-PAYMENT-RESPONSE>', advertisedSchema:{type:'object', " +
    "required:['name']}}) -> {delivery_verdict: 'consistent', payment: {bound: true, " +
    "transaction: '0x…'}, attestation: {…}}.",
  {
    service: z.string().min(1).max(500)
      .describe("URL (or name) of the paid service whose delivery is being verified."),
    responseText: z.string().min(1)
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
