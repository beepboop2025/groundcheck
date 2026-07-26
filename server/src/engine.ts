// HTTP client to the Python evidence engine. The MCP server holds no logic of its own;
// retrieval, stance, and the verdict all live behind these calls.
import type { CheckResult, DeliveryResult, ExtractResult, ResolveResult, VerifyResult } from "./types.js";

export const ENGINE_URL = process.env.GROUNDCHECK_ENGINE_URL ?? "http://127.0.0.1:8723";

// A hosted engine answered 402: the tool call is fine, payment is the missing
// piece. Tool handlers turn this into an isError result carrying the offer —
// never a thrown protocol error the agent can't read.
export class PaymentRequiredError extends Error {
  readonly offer: Record<string, unknown> | null;
  constructor(message: string, offer: Record<string, unknown> | null) {
    super(message);
    this.name = "PaymentRequiredError";
    this.offer = offer;
  }
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${ENGINE_URL}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 402) {
    // Hosted engines may charge per call via x402. Surface the offer instead
    // of a bare error, and point at the always-free path. The body may be a
    // v1 envelope (maxAmountRequired) or a v2 one (amount) — read both.
    const info = (await res.json().catch(() => null)) as {
      error?: string;
      accepts?: Array<{ maxAmountRequired?: string; amount?: string; network?: string }>;
    } | null;
    const offer = info?.accepts?.[0];
    const atomic = offer?.maxAmountRequired ?? offer?.amount;
    const usd = atomic ? Number(atomic) / 1e6 : undefined;
    throw new PaymentRequiredError(
      `This tool is paid on the hosted engine (x402)${usd ? `: $${usd} USDC per call on ${offer?.network}` : ""}. ` +
        `${info?.error ?? ""} Retry with an X-PAYMENT header (offers at ${ENGINE_URL}/.well-known/x402), ` +
        `use the free verify_claim tool, or run a local engine — it is free: ` +
        `https://github.com/beepboop2025/groundcheck`,
      (info as Record<string, unknown> | null) ?? null,
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

export function extractClaims(text: string, maxClaims = 20): Promise<ExtractResult> {
  return postJson<ExtractResult>("/extract", { text, max_claims: maxClaims });
}

export function attestDelivery(input: {
  service: string;
  responseText: string;
  requestText?: string;
  paymentReceipt?: string;
  advertisedSchema?: Record<string, unknown>;
  maxClaims?: number;
}): Promise<DeliveryResult> {
  return postJson<DeliveryResult>("/attest-delivery", {
    service: input.service,
    response_text: input.responseText,
    request_text: input.requestText ?? null,
    payment_receipt: input.paymentReceipt ?? null,
    advertised_schema: input.advertisedSchema ?? null,
    max_claims: input.maxClaims ?? 8,
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
