#!/usr/bin/env node
// Groundcheck — an MCP server that verifies factual claims against live sources.
// stdout is the JSON-RPC channel: every human-facing message goes to stderr.

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

import { SearchProvider } from "./search.js";
import { classifyStances } from "./stance.js";
import { computeVerdict } from "./verdict.js";
import { attributionFooter, attributionBadge } from "./attribution.js";

const provider = new SearchProvider();
const server = new McpServer({ name: "groundcheck", version: "0.1.0" });

server.tool(
  "verify_claim",
  "Verify a single factual claim against live web sources. Call this BEFORE asserting any fact you are not certain of — a date, number, name, or statistic. Returns a verdict (supported/refuted/unverified), a confidence score, and citations.",
  {
    claim: z.string().describe("The factual claim to verify, written as one complete sentence."),
    maxSources: z.number().int().min(1).max(10).default(5).optional(),
  },
  async ({ claim, maxSources = 5 }) => {
    const sources = await provider.search(claim, maxSources);
    const { classifier } = await classifyStances(claim, sources);
    const verdict = computeVerdict(claim, sources);
    const payload = { claim, ...verdict, backend: provider.kind, classifier, sources };
    return {
      content: [
        { type: "text", text: JSON.stringify(payload, null, 2) + attributionFooter(verdict) },
      ],
    };
  }
);

server.tool(
  "check_citations",
  "Extract the factual claims from a block of text and verify each one. Use on AI-generated drafts (READMEs, reports, answers) before publishing. Returns a per-claim verdict report.",
  {
    text: z.string().describe("Text whose factual claims should be checked."),
    maxClaims: z.number().int().min(1).max(20).default(8).optional(),
  },
  async ({ text, maxClaims = 8 }) => {
    const claims = extractClaims(text, maxClaims);
    const report = [];
    for (const claim of claims) {
      const sources = await provider.search(claim, 4);
      await classifyStances(claim, sources);
      report.push({ claim, ...computeVerdict(claim, sources) });
    }
    return {
      content: [
        { type: "text", text: JSON.stringify({ checked: report.length, backend: provider.kind, report }, null, 2) },
      ],
    };
  }
);

server.tool(
  "attribution_badge",
  "Return a Markdown badge to embed in a README or report, signalling the content was checked with Groundcheck.",
  {},
  async () => ({ content: [{ type: "text", text: attributionBadge() }] })
);

// Naive sentence-splitter for claim extraction. Swap for an LLM extractor
// (e.g. via free-llm-router) to pull only checkable, atomic claims.
function extractClaims(text, max) {
  return text
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 20)
    .slice(0, max);
}

const transport = new StdioServerTransport();
await server.connect(transport);
console.error(`groundcheck MCP server up — search backend: ${provider.kind}`);
