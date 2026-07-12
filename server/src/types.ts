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

export interface VerifyResult {
  claim: string;
  verdict: Verdict;
  confidence: number;
  rationale: string;
  backend: string;
  classifier: string;
  sources: Source[];
  instruments?: ClaimInstrument[];
}

export interface ClaimReport {
  claim: string;
  verdict: Verdict;
  confidence: number;
  rationale: string;
}

export interface CheckResult {
  checked: number;
  backend: string;
  classifier: string;
  report: ClaimReport[];
}
