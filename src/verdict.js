/**
 * computeVerdict — the brain of Groundcheck.
 *
 * Given a claim and its stance-tagged sources, decides:
 *   - verdict:    "supported" | "refuted" | "unverified"
 *   - confidence: a number in [0, 1]
 *   - rationale:  one short human-readable string
 *
 * Each source: { stance: "supports"|"refutes"|"neutral"|null, stub?: true, ... }
 *
 * The four design decisions, and how this implementation resolves them:
 *
 *   1. NON-EVIDENCE pulls toward unverified. `stub` sources are dropped; `null`/neutral
 *      stances never create a verdict. An unconfigured backend can NEVER look "supported".
 *   2. CONFLICT is refused, not majority-voted. If ANY source supports AND any refutes,
 *      the verdict is "unverified (disputed)" — repeating a contested claim confidently
 *      is the worst failure a fact-checker can make.
 *   3. ONE source is a lean, not a ruling. A single supporting source caps confidence at
 *      SINGLE_CAP; you need ≥2 agreeing sources to exceed it.
 *   4. CONFIDENCE SATURATES. Going 2→3→4 agreeing sources adds less each time
 *      (diminishing returns), rather than scaling linearly to 1.0.
 *
 * Tune the three constants below to move the cautious/decisive trade-off.
 */
const SINGLE_CAP = 0.6; // max confidence from a lone supporting/refuting source
const DECAY = 0.45; // smaller = confidence rises faster with more agreeing sources
const CONFLICT_CONF = 0.25; // confidence reported when sources disagree

// Saturating curve: 1->0.55, 2->0.80, 3->0.91 (with DECAY=0.45). Never reaches 1.
const saturate = (n) => 1 - Math.pow(DECAY, n);

export function computeVerdict(claim, sources) {
  const evidence = sources.filter((s) => !s.stub);
  if (evidence.length === 0) {
    return { verdict: "unverified", confidence: 0, rationale: "No live sources." };
  }

  const supports = evidence.filter((s) => s.stance === "supports").length;
  const refutes = evidence.filter((s) => s.stance === "refutes").length;

  // (2) Conflict → refuse to call it.
  if (supports > 0 && refutes > 0) {
    return {
      verdict: "unverified",
      confidence: CONFLICT_CONF,
      rationale: `Sources disagree (${supports} support, ${refutes} refute).`,
    };
  }

  // (1) Only neutral/unknown stances → evidence exists but says nothing decisive.
  if (supports === 0 && refutes === 0) {
    return {
      verdict: "unverified",
      confidence: 0.15,
      rationale: `${evidence.length} source(s) found, none took a clear stance.`,
    };
  }

  const [verdict, n] = supports > 0 ? ["supported", supports] : ["refuted", refutes];
  // (3)(4) Cap a lone source; saturate beyond.
  const confidence = n === 1 ? Math.min(SINGLE_CAP, saturate(1)) : saturate(n);
  const verb = verdict === "supported" ? "support" : "refute";
  return {
    verdict,
    confidence: Number(confidence.toFixed(2)),
    rationale: `${n} source(s) ${verb} the claim, none disagree.`,
  };
}
