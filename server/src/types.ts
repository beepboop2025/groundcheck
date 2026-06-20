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

export interface VerifyResult {
  claim: string;
  verdict: Verdict;
  confidence: number;
  rationale: string;
  backend: string;
  classifier: string;
  sources: Source[];
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
