// Wire types for the engine's JSON responses. Mirror of engine/groundcheck_engine/models.py.

export type Stance = "supports" | "refutes" | "neutral";
export type Verdict = "supported" | "refuted" | "unverified";

export interface Source {
  title: string;
  url: string;
  snippet: string;
  stance: Stance | null;
  stub?: boolean;
}

export interface Instrument {
  figi: string;
  name: string | null;
  ticker: string | null;
  exch_code: string | null;
  security_type: string | null;
  market_sector: string | null;
  composite_figi: string | null;
  share_class_figi: string | null;
  description: string | null;
}

export interface ClaimInstrument {
  reference: string;
  id_type: string;
  resolved: boolean;
  instrument: Instrument | null;
}

export interface ResolveResult {
  query: string;
  id_type: string | null;
  matched: boolean;
  instruments: Instrument[];
  provenance: {
    source: string;
    url: string;
    retrieved_at: string;
    authenticated: boolean;
  };
  note: string | null;
}

// Conformal certification of a verdict (engine conformal.py). Present only on
// calibrated deployments; certified=true carries a finite-sample guarantee
// that the error probability for this verdict direction is <= alpha.
export interface Guarantee {
  certified: boolean;
  alpha: number;
  group: string;
  score?: number | null;
  threshold?: number | null;
  n_calibration?: number | null;
  calibrated_at?: string | null;
}

export type Sufficiency =
  | "sufficient" | "insufficient" | "no_sources" | "no_stance" | "conflict";

export interface AtomReport {
  claim: string;
  verdict: Verdict;
  confidence: number;
  sufficiency?: Sufficiency | null;
}

export interface VerifyResult {
  claim: string;
  verdict: Verdict;
  confidence: number;
  rationale: string;
  backend: string;
  classifier: string;
  sources: Source[];
  instruments?: ClaimInstrument[];
  // Why the verdict is (not) directional (SURE-RAG three-way distinction).
  sufficiency?: Sufficiency | null;
  // Present when a compound claim was split into independently-verified atoms.
  atoms?: AtomReport[] | null;
  // Weighted multi-model panel probability that the claim is true.
  ensemble_score?: number | null;
  guarantee?: Guarantee | null;
  // Evidence-bound provenance: rolling commitment over the evidence path +
  // model route, bound into the signed manifest.
  provenance?: Record<string, unknown> | null;
  // Signed Ed25519 receipt over a deterministic subset of the response
  // (engine attest.py). Passed through verbatim; verify offline via the
  // engine's GET /attest/pubkey.
  attestation?: Record<string, unknown> | null;
}

export interface ClaimReport {
  claim: string;
  verdict: Verdict;
  confidence: number;
  rationale: string;
  sufficiency?: Sufficiency | null;
  guarantee?: Guarantee | null;
}

export interface CheckResult {
  checked: number;
  backend: string;
  classifier: string;
  report: ClaimReport[];
  // See VerifyResult.attestation.
  attestation?: Record<string, unknown> | null;
}
