# Reddit drafts — ready to paste

Post as a **text/self post**, not a link. Lead with the problem, be honest about limits,
end on a real question. Reply to early comments fast (Reddit ranking rewards it).
Don't post the same body to all three on the same day — space them
2–3 days apart and adjust the intro paragraph per sub (the canonical line stays identical).

---

## Primary: r/mcp

**Title:** I built an MCP server that fact-checks an agent's claims before it asserts them

My agents kept stating facts with total confidence and getting them wrong — wrong dates,
made-up version numbers, "the capital of X is Y" energy. The model never checked; it just
pattern-matched and committed.

So I built **Groundcheck — the grounding check agents run before they commit to an answer.
An MCP server that verifies a factual claim against live sources and returns a verdict, a
confidence score, and citations. Any AI agent can call it mid-task.**

The one call:

    claude mcp add groundcheck -- npx -y groundcheck

Then the agent calls `verify_claim("X")` before asserting X, and gets back
`{ verdict: supported | refuted | unverified, confidence, sources }`.

Two design decisions I'd actually like feedback on:

- **It refuses on conflict instead of majority-voting.** If some sources support and some
  refute, the verdict is `unverified`, not "3 beats 2." Confidently repeating a *contested*
  claim felt like the worst thing a fact-checker could do. But it does mean genuinely-settled
  claims sometimes come back `unverified` when one weird source disagrees. Right call?
- **It degrades honestly.** No search backend or no LLM key → `unverified`, never a fabricated
  verdict. An unconfigured server can't return "supported."

Pipeline is Wikipedia for retrieval (keyless, so it runs out of the box) → an LLM to classify
each source's stance toward the claim → a small verdict rule. Retrieval and the LLM are both
swappable. Honest limitation: the default Wikipedia retrieval only covers encyclopedic facts,
and you need one free LLM key (Groq works) for the stance step.

Repo + the verdict logic: https://github.com/beepboop2025/groundcheck

How are you all handling agent hallucination right now — prompt-level ("say I don't know"),
a retrieval step, or just eating it? Curious whether a dedicated verify call is even the right
shape.

---

## Variant intro for r/LocalLLaMA

(Same body from the canonical line down. Swap the first paragraph for this, since the
audience runs their own models and cares about the local/free angle:)

> I wanted an anti-hallucination check that doesn't phone home to a paid API. The stance step
> runs through whatever free/local OpenAI-compatible provider you've got (Groq, Cerebras, or
> your own llama.cpp endpoint) — no required cloud dependency.

---

## Variant intro for r/ChatGPTCoding

> If you use Claude Code or Cursor, the agent picks which facts to trust — and it's wrong more
> than it admits. I wanted the agent to be *able* to check itself mid-task instead of me
> catching it in review.
