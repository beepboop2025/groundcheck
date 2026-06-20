#!/usr/bin/env node
// Groundcheck — MCP server (thin protocol layer). stdout is the JSON-RPC channel;
// every human-facing message goes to stderr. All logic lives in the Python engine.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

import { ENGINE_URL, checkCitations, engineReachable, verifyClaim } from "./engine.js";
import { attributionBadge, attributionFooter } from "./attribution.js";
import type { VerifyResult } from "./types.js";

const server = new McpServer({ name: "groundcheck", version: "0.2.0" });

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
  "Verify a single factual claim against live sources. Call this BEFORE asserting any fact you are not certain of. Returns a verdict (supported/refuted/unverified), a confidence score, and citations.",
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
  "Extract the factual claims from a block of text and verify each one. Use on AI-generated drafts before publishing. Returns a per-claim verdict report.",
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
  "attribution_badge",
  "Return a Markdown badge to embed in a README or report, signalling the content was checked with Groundcheck.",
  {},
  async () => ({ content: [{ type: "text", text: attributionBadge() }] })
);

const transport = new StdioServerTransport();
await server.connect(transport);
const reachable = await engineReachable();
console.error(
  `groundcheck MCP server up — engine ${ENGINE_URL} ${reachable ? "reachable ✓" : "UNREACHABLE (start it: docker compose up -d / make engine)"}`
);
