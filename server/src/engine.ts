// HTTP client to the Python evidence engine. The MCP server holds no logic of its own;
// retrieval, stance, and the verdict all live behind these calls.
import type { CheckResult, VerifyResult } from "./types.js";

export const ENGINE_URL = process.env.GROUNDCHECK_ENGINE_URL ?? "http://127.0.0.1:8723";

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${ENGINE_URL}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`engine ${res.status}: ${await res.text().catch(() => "")}`);
  }
  return (await res.json()) as T;
}

export function verifyClaim(claim: string, maxSources = 5): Promise<VerifyResult> {
  return postJson<VerifyResult>("/verify", { claim, max_sources: maxSources });
}

export function checkCitations(text: string, maxClaims = 8): Promise<CheckResult> {
  return postJson<CheckResult>("/check", { text, max_claims: maxClaims });
}

export async function engineReachable(): Promise<boolean> {
  try {
    const res = await fetch(`${ENGINE_URL}/health`);
    return res.ok;
  } catch {
    return false;
  }
}
