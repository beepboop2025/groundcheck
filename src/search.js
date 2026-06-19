// SearchProvider — the evidence-gathering layer.
//
// Backend resolution (first match wins):
//   1. GROUNDCHECK_SEARCH_BACKEND=stub  → honest placeholder, never verifies.
//   2. GROUNDCHECK_SEARCH_URL set       → your custom JSON search endpoint.
//   3. default                          → Wikipedia (keyless, works out of the box).
//
// Every source is { title, url, snippet, stance:null }. Stance ("supports" |
// "refutes" | "neutral") is filled in later by src/stance.js — retrieval only
// gathers evidence, it does not judge it.

const WIKI_API = "https://en.wikipedia.org/w/api.php";

export class SearchProvider {
  constructor({
    backend = process.env.GROUNDCHECK_SEARCH_BACKEND,
    searchUrl = process.env.GROUNDCHECK_SEARCH_URL,
    apiKey = process.env.GROUNDCHECK_SEARCH_KEY,
  } = {}) {
    this.apiKey = apiKey;
    this.searchUrl = searchUrl;
    this.kind = backend === "stub" ? "stub" : searchUrl ? "custom" : "wikipedia";
  }

  get isLive() {
    return this.kind !== "stub";
  }

  async search(query, maxSources = 5) {
    if (this.kind === "stub") return this.#stub(query, maxSources);
    if (this.kind === "custom") return this.#custom(query, maxSources);
    return this.#wikipedia(query, maxSources);
  }

  async #wikipedia(query, maxSources) {
    const url = new URL(WIKI_API);
    url.search = new URLSearchParams({
      action: "query",
      list: "search",
      srsearch: query,
      srlimit: String(maxSources),
      format: "json",
      origin: "*",
    }).toString();

    const res = await fetch(url, { headers: { "user-agent": "groundcheck/0.1 (MCP fact-check)" } });
    if (!res.ok) throw new Error(`wikipedia ${res.status}: ${await res.text().catch(() => "")}`);
    const data = await res.json();
    return (data.query?.search ?? []).slice(0, maxSources).map((r) => ({
      title: r.title ?? "",
      url: `https://en.wikipedia.org/?curid=${r.pageid}`,
      snippet: stripHtml(r.snippet ?? ""),
      stance: null,
    }));
  }

  async #custom(query, maxSources) {
    const url = new URL(this.searchUrl);
    url.searchParams.set("q", query);
    url.searchParams.set("n", String(maxSources));
    const res = await fetch(url, {
      headers: this.apiKey ? { Authorization: `Bearer ${this.apiKey}` } : {},
    });
    if (!res.ok) throw new Error(`search backend ${res.status}: ${await res.text().catch(() => "")}`);
    const data = await res.json();
    return (data.results ?? []).slice(0, maxSources).map((r) => ({
      title: r.title ?? "",
      url: r.url ?? "",
      snippet: r.snippet ?? "",
      stance: r.stance ?? null,
    }));
  }

  // Honest placeholder. `stub: true` is the signal that this is NOT evidence.
  #stub(query, maxSources) {
    return Array.from({ length: Math.min(2, maxSources) }, (_, i) => ({
      title: `[stub source ${i + 1}] search backend disabled`,
      url: "about:blank",
      snippet: `Unset GROUNDCHECK_SEARCH_BACKEND=stub to actually verify: "${query}".`,
      stance: null,
      stub: true,
    }));
  }
}

function stripHtml(s) {
  return s
    .replace(/<[^>]+>/g, "")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .replace(/&#0?39;|&apos;/g, "'")
    .replace(/&nbsp;/g, " ")
    .trim();
}
