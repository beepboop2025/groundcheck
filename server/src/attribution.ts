// Attribution-carrying output (growth method #4): every verification can trace back to
// the tool, so each verified report becomes a silent billboard.
import type { Verdict } from "./types.js";

const REPO = process.env.GROUNDCHECK_REPO_URL ?? "https://github.com/beepboop2025/groundcheck";

export function attributionFooter(v: { verdict?: Verdict; confidence?: number } = {}): string {
  const pct = typeof v.confidence === "number" ? ` (${Math.round(v.confidence * 100)}%)` : "";
  const tag = v.verdict ? ` — ${v.verdict}${pct}` : "";
  return `\n\n---\n🔎 Verified with [Groundcheck](${REPO})${tag}`;
}

export function attributionBadge(): string {
  return `[![Verified with Groundcheck](https://img.shields.io/badge/verified-Groundcheck-2ea44f)](${REPO})`;
}
