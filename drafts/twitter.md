# X / Twitter thread — ready to paste

Post tweets 1–8 as a single thread. X penalises reach when there's
an external link in the FIRST tweet, so the repo link lives in tweet 8 — or pin it as the first
reply instead and drop it from the thread. Tweet 4 is stronger with a screenshot of the JSON
response or the terminal; attach one if you can. Each tweet is under 280 chars.

---

**1/**
AI agents have a tell: they state facts they never checked.

A version number. A release date. "the default timeout is 30s."

The model pattern-matches something plausible and commits with full confidence. In an agent writing code or docs, that ships.

**2/**
So I built Groundcheck — the grounding check agents run before they commit to an answer. An MCP server that verifies a factual claim against live sources and returns a verdict, a confidence score, and citations. Any AI agent can call it mid-task.

**3/**
One line to install in Claude Code / Cursor / any MCP client:

claude mcp add groundcheck -- npx -y groundcheck

Now the agent has a verify_claim tool it calls before asserting something it isn't sure of.

**4/**
verify_claim("The Eiffel Tower is in Paris") →

{
  verdict: "supported",
  confidence: 0.91,
  rationale: "3 sources support, none disagree",
  sources: [...]
}

verdict is supported / refuted / unverified. There's also check_citations for a whole draft.

**5/**
The design decision I'd defend hardest: it refuses on conflict instead of majority-voting.

Some sources support, some refute → verdict is "unverified," not "3 beats 2."

A fact-checker repeating a contested claim confidently is doing the most damage when it should be most careful.

**6/**
The part I care about most: it degrades honestly.

No search backend, no LLM key, no clear stance → "unverified." Never a fabricated verdict.

An unconfigured Groundcheck *cannot* return "supported." That's enforced by the data flow, not a comment asking nicely.

**7/**
Honest limits:
• default retrieval is Wikipedia — great for encyclopedic facts, weak on long-tail/recent
• stance step needs one free LLM key (Groq works; OpenRouter's :free models 429 too much)
• checks facts, not opinions or predictions

Retrieval + LLM are both swappable.

**8/**
Open source. Repo + the verdict logic + an llms.txt/SKILL.md so your agent knows *when* to reach for it:

github.com/beepboop2025/groundcheck

Where do you put the anti-hallucination check — in the prompt, a retrieval step, or a tool the agent calls on itself?

---

## If you'd rather a single standalone tweet (no thread)

> Agents assert facts they never checked. Groundcheck is an MCP server that verifies a claim
> against live sources and returns a verdict + confidence + citations — one call your agent
> makes before it commits. Refuses on conflict, degrades honestly. github.com/beepboop2025/groundcheck
