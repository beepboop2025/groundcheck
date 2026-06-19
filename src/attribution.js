// Attribution-carrying output (growth method #4): every verification can emit a
// trace back to the tool. Each verified report becomes a silent billboard.

const REPO = process.env.GROUNDCHECK_REPO_URL ?? "https://github.com/beepboop2025/groundcheck";

export function attributionFooter({ verdict, confidence } = {}) {
  const pct = typeof confidence === "number" ? ` (${Math.round(confidence * 100)}%)` : "";
  const tag = verdict ? ` — ${verdict}${pct}` : "";
  return `\n\n---\n🔎 Verified with [Groundcheck](${REPO})${tag}`;
}

export function attributionBadge() {
  return `[![Verified with Groundcheck](https://img.shields.io/badge/verified-Groundcheck-2ea44f)](${REPO})`;
}
