// Stance classification — decides whether each retrieved source supports, refutes,
// or is neutral toward the claim. This is the step that needs an LLM, and it reuses
// your free-llm-router (server-only, free-tier providers) rather than a new dependency.
//
// Honest degradation: if the router module or any provider key is missing, stances
// stay null and the verdict falls back to "unverified" — it never guesses.

import { pathToFileURL } from "node:url";

const ROUTER_PATH =
  process.env.GROUNDCHECK_ROUTER_PATH ??
  "/Users/mrinal/free-llm-router/node/free-llm-router.mjs";

const VALID = new Set(["supports", "refutes", "neutral"]);

let _mod = null;
let _tried = false;
async function loadRouter() {
  if (_tried) return _mod;
  _tried = true;
  try {
    _mod = await import(pathToFileURL(ROUTER_PATH).href);
  } catch {
    _mod = null;
  }
  return _mod;
}

// Mutates `sources` in place, setting `.stance` where the classifier is confident.
// Returns a small meta object describing which classifier ran (for transparency).
export async function classifyStances(claim, sources) {
  const evidence = sources.filter((s) => !s.stub);
  if (evidence.length === 0) return { classifier: "none" };

  const mod = await loadRouter();
  if (!mod) return { classifier: "unavailable" }; // router not found at ROUTER_PATH
  const router = mod.getFreeRouter();
  if (!router.hasProviders) return { classifier: "no-providers" }; // no API keys set

  const numbered = evidence
    .map((s, i) => `[${i}] ${s.title} — ${s.snippet}`)
    .join("\n");

  const messages = [
    {
      role: "system",
      content:
        "You judge whether each source supports, refutes, or is neutral toward a factual claim. " +
        'Reply with ONLY a compact JSON array like [{"i":0,"stance":"supports"}]. ' +
        "stance is exactly one of: supports, refutes, neutral. Include every source index.",
    },
    { role: "user", content: `Claim: ${claim}\n\nSources:\n${numbered}` },
  ];

  let parsed;
  try {
    const res = await router.chatCompletion(messages, { taskType: "classification", temperature: 0 });
    parsed = parseStances(res.text);
  } catch (err) {
    return { classifier: "error", error: String(err) };
  }

  for (let i = 0; i < evidence.length; i++) {
    if (VALID.has(parsed[i])) evidence[i].stance = parsed[i];
  }
  return { classifier: "free-llm-router" };
}

function parseStances(text) {
  const match = text.match(/\[[\s\S]*\]/); // first JSON array in the reply
  if (!match) return {};
  try {
    const arr = JSON.parse(match[0]);
    const out = {};
    for (const o of arr) {
      if (o && typeof o.i === "number") out[o.i] = String(o.stance ?? "").toLowerCase();
    }
    return out;
  } catch {
    return {};
  }
}
