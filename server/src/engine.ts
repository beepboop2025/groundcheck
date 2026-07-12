// HTTP client to the Python evidence engine. The MCP server holds no logic of its own;
// retrieval, stance, and the verdict all live behind these calls.
import type { CheckResult, ResolveResult, VerifyResult } from "./types.js";

export const ENGINE_URL = process.env.GROUNDCHECK_ENGINE_URL ?? "http://127.0.0.1:8723";

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${ENGINE_URL}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 402) {
    // Hosted engines may charge per call via x402. Surface the offer instead
    // of a bare error, and point at the always-free path.
    const info = (await res.json().catch(() => null)) as {
      error?: string;
      accepts?: Array<{ maxAmountRequired?: string; network?: string }>;
    } | null;
    const offer = info?.accepts?.[0];
    const usd = offer?.maxAmountRequired ? Number(offer.maxAmountRequired) / 1e6 : undefined;
    throw new Error(
      `engine requires payment (x402)${usd ? `: $${usd} USDC per call on ${offer?.network}` : ""}. ` +
        `${info?.error ?? ""} Retry with an X-PAYMENT header (see /.well-known/x402 on the engine), ` +
        `or run a local engine — it is free: https://github.com/beepboop2025/groundcheck`,
    );
  }
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

export function resolveInstrument(
  query: string,
  idType?: string,
  maxResults = 5,
): Promise<ResolveResult> {
  return postJson<ResolveResult>("/resolve", {
    query,
    id_type: idType ?? null,
    max_results: maxResults,
  });
}

export async function engineReachable(): Promise<boolean> {
  try {
    const res = await fetch(`${ENGINE_URL}/health`);
    return res.ok;
  } catch {
    return false;
  }
}
