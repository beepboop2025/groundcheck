# Consumer: Narrative Divergence Detector

The **Narrative Divergence Detector** ([`beepboop2025/farmctl`](https://github.com/beepboop2025/farmctl), `narrative/`) uses groundcheck as its **verifier** — the component that decides whether a tracked claim is actually false.

## Integration

- `narrative/verify.py` ships `GroundcheckVerifier(base_url)`, which POSTs to this engine's `/verify` endpoint:
  - request: `{ "claim": "<one sentence>", "maxSources": 5 }`
  - response consumed: `verdict` (`supported` | `refuted` | `unverified`), `confidence`, `sources`, `classifier`
- The verdict is a **multiplier** in the detector's concern score, not just an addend: a claim verified *true* stays low-concern no matter how viral, while a *refuted* claim with high confidence amplifies concern. So groundcheck's accuracy directly gates what the detector escalates.
- A `MockVerifier` mirrors the same contract for offline tests, so the detector runs without a live groundcheck server.

## Why this matters for groundcheck

This extends groundcheck from "is this one claim true?" to powering a defensive, observe-and-verify pipeline over **coordinated, audience-tailored narratives** (influence-campaign detection) — while keeping groundcheck's role strictly evidentiary. The detector reports pattern-based salience, never attribution of intent; groundcheck supplies the falsity evidence that anchors it.

No groundcheck code changes are required — the existing `/verify` HTTP contract and pluggable retrieval backend (`GROUNDCHECK_SEARCH_URL`) are sufficient.
