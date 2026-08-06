"""The interactive landing page served at GET / — a live fact-check demo."""

LANDING_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="base:app_id" content="6a52dd0ce3b311a8d678de9d" />
<title>Groundcheck | Live-source claim and citation verification</title>
<meta name="description" content="Groundcheck verifies factual claims against live sources and returns supported, refuted, or unverified verdicts with confidence scores and citations over MCP." />
<meta name="robots" content="index, follow, max-image-preview:large" />
<link rel="canonical" href="https://groundcheck.seiche.info/" />
<meta property="og:type" content="website" />
<meta property="og:site_name" content="Groundcheck" />
<meta property="og:title" content="Groundcheck | Live-source claim and citation verification" />
<meta property="og:description" content="Verify factual claims against live sources and receive a verdict, confidence score, and citations over MCP." />
<meta property="og:url" content="https://groundcheck.seiche.info/" />
<meta name="twitter:card" content="summary" />
<meta name="twitter:title" content="Groundcheck | Live-source claim verification" />
<meta name="twitter:description" content="Supported, refuted, or unverified verdicts with confidence scores and citations over MCP." />
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "WebApplication",
  "name": "Groundcheck",
  "url": "https://groundcheck.seiche.info/",
  "applicationCategory": "DeveloperApplication",
  "operatingSystem": "Any",
  "description": "Live-source claim verification over MCP, with supported, refuted, or unverified verdicts, confidence scores, and citations.",
  "codeRepository": "https://github.com/beepboop2025/groundcheck",
  "license": "https://opensource.org/license/mit",
  "featureList": [
    "Claim verification against retrieved sources",
    "Citation checks for multi-claim drafts",
    "MCP and HTTP access",
    "Signed delivery receipts for paid agent services"
  ]
}
</script>
<style>
  :root { --bg:#0a0e14; --panel:#111824; --hi:#e6edf3; --mid:#8b97a7; --dim:#5b6675; --green:#3fb950; --blue:#79c0ff; --red:#f85149; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body {
    font-family:-apple-system,"SF Pro Display","Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:
      radial-gradient(1100px 520px at 78% -10%, rgba(63,185,80,.10), transparent 60%),
      linear-gradient(160deg,#0a0e14,#0c1119 55%,#090d13);
    color:var(--hi); min-height:100vh; line-height:1.5; padding:48px 20px;
  }
  .wrap { max-width:720px; margin:0 auto; }
  .eyebrow { font:600 13px/1 "SF Mono",Menlo,monospace; letter-spacing:3px; text-transform:uppercase; color:var(--dim); display:flex; align-items:center; gap:10px; }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--green); box-shadow:0 0 10px var(--green); }
  h1 { font-size:54px; font-weight:800; letter-spacing:-2px; margin:18px 0 6px; }
  h1 .k { color:var(--green); }
  .tag { font-size:20px; color:var(--mid); max-width:560px; }
  .definition { margin-top:16px; max-width:650px; color:var(--mid); font-size:15px; }
  .demo { margin:36px 0 14px; background:var(--panel); border:1px solid rgba(255,255,255,.08); border-radius:16px; padding:22px; }
  .demo label { font:600 12px/1 "SF Mono",Menlo,monospace; letter-spacing:1px; text-transform:uppercase; color:var(--dim); }
  .row { display:flex; gap:10px; margin-top:10px; }
  input { flex:1; background:#0a0f17; border:1px solid rgba(255,255,255,.12); border-radius:10px; padding:13px 14px; color:var(--hi); font-size:16px; }
  input:focus { outline:none; border-color:var(--green); }
  button { background:var(--green); color:#06210d; border:none; border-radius:10px; padding:0 22px; font-size:16px; font-weight:700; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .out { margin-top:18px; display:none; }
  .verdict { display:flex; align-items:center; gap:10px; font:700 26px/1.1 "SF Mono",Menlo,monospace; }
  .verdict.supported { color:var(--green); } .verdict.refuted { color:var(--red); } .verdict.unverified { color:var(--mid); }
  .pill { margin-left:auto; font:700 14px/1 "SF Mono",Menlo,monospace; padding:5px 11px; border-radius:999px; border:1px solid currentColor; }
  .rationale { color:var(--mid); margin-top:10px; font-size:15px; }
  .sources { margin-top:14px; display:flex; flex-direction:column; gap:8px; }
  .src { font-size:14px; color:var(--mid); }
  .src a { color:var(--blue); text-decoration:none; }
  .src .st { font:600 11px/1 monospace; text-transform:uppercase; padding:2px 7px; border-radius:6px; margin-right:8px; border:1px solid var(--dim); color:var(--dim); }
  .links { margin-top:30px; font-size:14px; color:var(--dim); }
  .links a { color:var(--mid); }
  .ex { margin-top:10px; font-size:13px; color:var(--dim); }
  .ex span { color:var(--blue); cursor:pointer; }
  .facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:18px; }
  .fact { border:1px solid rgba(255,255,255,.08); border-radius:12px; padding:16px; background:rgba(17,24,36,.55); }
  .fact h2 { font-size:14px; margin-bottom:6px; }
  .fact p { color:var(--mid); font-size:13px; }
  @media (max-width:600px) { h1 { font-size:42px; } .row { flex-direction:column; } button { min-height:44px; } .facts { grid-template-columns:1fr; } }
</style>
</head>
<body>
<div class="wrap">
  <div class="eyebrow"><span class="dot"></span> Live-source verification · MCP and HTTP</div>
  <h1>Ground<span class="k">check</span></h1>
  <p class="tag">Verify a factual claim against live sources. Groundcheck returns a verdict, confidence score, and citations.</p>
  <p class="definition">Groundcheck retrieves evidence before classifying a claim as supported, refuted, or unverified. Missing, weak, or conflicting evidence stays unverified instead of being turned into a confident answer.</p>

  <div class="demo">
    <label>Try it: verify a factual claim</label>
    <div class="row">
      <input id="claim" placeholder="The Eiffel Tower is located in Paris, France." />
      <button id="go">Verify</button>
    </div>
    <div class="ex">e.g. <span data-c="The Great Wall of China is visible from space with the naked eye.">a tricky one</span> · <span data-c="Python is a programming language created by Guido van Rossum.">an easy one</span></div>
    <div class="out" id="out"></div>
  </div>

  <section class="facts" aria-label="Groundcheck capabilities">
    <article class="fact"><h2>Claim verdicts</h2><p>Check one claim or extract and check the factual claims in a draft.</p></article>
    <article class="fact"><h2>Source trail</h2><p>Every result includes the passages and links used to reach the verdict.</p></article>
    <article class="fact"><h2>Agent access</h2><p>Use the hosted MCP endpoint or call the HTTP API directly.</p></article>
    <article class="fact"><h2>Explicit abstention</h2><p>No sources, insufficient support, and source conflict remain distinct failure states.</p></article>
  </section>

  <div class="links">
    Install: <code>claude mcp add groundcheck -- npx -y groundcheck-mcp</code><br/>
    <a href="https://github.com/beepboop2025/groundcheck">Source and method</a> · <a href="/health">Health</a> · <a href="/docs">API docs</a>
  </div>
</div>

<script>
const $ = (s) => document.querySelector(s);
const out = $("#out"), input = $("#claim"), btn = $("#go");
document.querySelectorAll(".ex span").forEach(s => s.onclick = () => { input.value = s.dataset.c; verify(); });
btn.onclick = verify;
input.addEventListener("keydown", (e) => { if (e.key === "Enter") verify(); });

// Build the DOM with textContent (never innerHTML) so engine/search-derived
// strings can't inject markup, and only allow http(s) source links.
const VERDICTS = { supported: 1, refuted: 1, unverified: 1 };
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function safeHref(url) {
  try { const u = new URL(url); return (u.protocol === "http:" || u.protocol === "https:") ? u.href : null; }
  catch { return null; }
}
function msg(text) { out.textContent = ""; out.appendChild(el("div", "rationale", text)); }

async function verify() {
  const claim = input.value.trim() || input.placeholder;
  btn.disabled = true; btn.textContent = "…";
  out.style.display = "block";
  msg("checking against live sources…");
  try {
    const r = await fetch("/verify", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ claim, max_sources: 4 }) });
    if (r.status === 429) { msg("Rate limited. Try again in a minute."); return; }
    render(await r.json());
  } catch (e) {
    msg("Error reaching the engine.");
  } finally {
    btn.disabled = false; btn.textContent = "Verify";
  }
}

function render(j) {
  const pct = Math.round((j.confidence || 0) * 100);
  out.textContent = "";

  const vClass = VERDICTS[j.verdict] ? j.verdict : "unverified";
  const vRow = el("div", "verdict " + vClass);
  vRow.appendChild(el("span", null, j.verdict || "unverified"));
  vRow.appendChild(el("span", "pill", pct + "%"));
  out.appendChild(vRow);

  const rat = el("div", "rationale", (j.rationale || "") + " ");
  rat.appendChild(el("em", null, "(via " + j.backend + " · " + j.classifier + ")"));
  out.appendChild(rat);

  const wrap = el("div", "sources");
  (j.sources || []).forEach(s => {
    const row = el("div", "src");
    row.appendChild(el("span", "st", s.stance || "—"));
    const href = safeHref(s.url);
    if (href) {
      const a = el("a", null, s.title || s.url);
      a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
      row.appendChild(a);
    } else {
      row.appendChild(el("span", null, s.title || s.url || ""));
    }
    wrap.appendChild(row);
  });
  out.appendChild(wrap);
}
</script>
</body>
</html>
"""
