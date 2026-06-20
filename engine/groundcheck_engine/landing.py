"""The interactive landing page served at GET / — a live fact-check demo."""

LANDING_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Groundcheck — the grounding check agents run before they commit to an answer</title>
<meta name="description" content="Groundcheck verifies a factual claim against live sources and returns a verdict, a confidence score, and citations — over MCP, so any AI agent can call it mid-task." />
<meta property="og:title" content="Groundcheck" />
<meta property="og:description" content="The grounding check agents run before they commit to an answer." />
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
</style>
</head>
<body>
<div class="wrap">
  <div class="eyebrow"><span class="dot"></span> Model Context Protocol Server</div>
  <h1>Ground<span class="k">check</span></h1>
  <p class="tag">The grounding check agents run before they commit to an answer. Verify a claim against live sources — verdict, confidence, citations.</p>

  <div class="demo">
    <label>Try it — verify a factual claim</label>
    <div class="row">
      <input id="claim" placeholder="The Eiffel Tower is located in Paris, France." />
      <button id="go">Verify</button>
    </div>
    <div class="ex">e.g. <span data-c="The Great Wall of China is visible from space with the naked eye.">a tricky one</span> · <span data-c="Python is a programming language created by Guido van Rossum.">an easy one</span></div>
    <div class="out" id="out"></div>
  </div>

  <div class="links">
    Install: <code>claude mcp add groundcheck -- npx -y groundcheck</code><br/>
    <a href="https://github.com/beepboop2025/groundcheck">GitHub</a> · <a href="/health">/health</a> · <a href="/docs">API docs</a>
  </div>
</div>

<script>
const $ = (s) => document.querySelector(s);
const out = $("#out"), input = $("#claim"), btn = $("#go");
document.querySelectorAll(".ex span").forEach(s => s.onclick = () => { input.value = s.dataset.c; verify(); });
btn.onclick = verify;
input.addEventListener("keydown", (e) => { if (e.key === "Enter") verify(); });

async function verify() {
  const claim = input.value.trim() || input.placeholder;
  btn.disabled = true; btn.textContent = "…";
  out.style.display = "block";
  out.innerHTML = '<div class="rationale">checking against live sources…</div>';
  try {
    const r = await fetch("/verify", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ claim, max_sources: 4 }) });
    if (r.status === 429) { out.innerHTML = '<div class="rationale">Rate limited — try again in a minute.</div>'; return; }
    const j = await r.json();
    const pct = Math.round((j.confidence || 0) * 100);
    const srcs = (j.sources || []).map(s =>
      `<div class="src"><span class="st">${s.stance || "—"}</span><a href="${s.url}" target="_blank">${s.title || s.url}</a></div>`).join("");
    out.innerHTML =
      `<div class="verdict ${j.verdict}"><span>${j.verdict}</span><span class="pill">${pct}%</span></div>` +
      `<div class="rationale">${j.rationale} <em>(via ${j.backend} · ${j.classifier})</em></div>` +
      `<div class="sources">${srcs}</div>`;
  } catch (e) {
    out.innerHTML = '<div class="rationale">Error reaching the engine.</div>';
  } finally {
    btn.disabled = false; btn.textContent = "Verify";
  }
}
</script>
</body>
</html>
"""
