"""The dashboard's single page. Plain HTML + JS, no external assets (works
offline on the laptop). Every value is inserted with `textContent`, never
`innerHTML`, so text from the database (news titles, broker comments,
incident details) cannot inject markup."""

PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Adaptive Scalper Next</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --fg:#1d2330; --muted:#667085; --line:#e4e7ec;
          --ok:#127a3e; --warn:#a15c00; --bad:#b42318; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1115; --card:#171a21; --fg:#e6e8ee; --muted:#98a2b3; --line:#2a2f3a;
            --ok:#4ade80; --warn:#fbbf24; --bad:#f87171; }
  }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.45 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
  header { padding:12px 16px; border-bottom:1px solid var(--line); display:flex; flex-wrap:wrap; gap:12px;
           align-items:center; }
  header h1 { font-size:16px; margin:0 12px 0 0; }
  .pill { padding:2px 10px; border-radius:999px; border:1px solid var(--line); font-weight:600; }
  .ok { color:var(--ok); } .warn { color:var(--warn); } .bad { color:var(--bad); }
  main { display:grid; grid-template-columns:repeat(auto-fill, minmax(360px, 1fr)); gap:12px; padding:12px 16px; }
  section { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px;
            overflow:auto; max-height:460px; }
  section h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; margin:0 0 6px; color:var(--muted); }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  td, th { border-top:1px solid var(--line); padding:3px 6px; text-align:left; vertical-align:top; }
  th { color:var(--muted); font-weight:600; }
  .kv td:first-child { color:var(--muted); width:40%; }
  .empty { color:var(--muted); font-style:italic; }
  #conn { margin-left:auto; color:var(--muted); font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>Adaptive Scalper Next</h1>
  <span class="pill bad">REAL-MONEY EXECUTION: DISABLED</span>
  <span class="pill" id="health">health: ...</span>
  <span class="pill" id="kill">kill switch: ...</span>
  <span class="pill" id="runtime">runtime: ...</span>
  <span id="conn">connecting...</span>
</header>
<main id="panels"></main>
<script>
"use strict";
const ORDER = ["overview","components","symbols","positions","orders","decisions","risk","news","events",
               "costs","research","learning","memory","knowledge","history"];
function el(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls; return e; }
function fmt(v) {
  if (v === null || v === undefined) return "\\u2014";
  if (typeof v === "number" && v > 1e9 && v < 1e10 && Number.isInteger(v)) return new Date(v*1000).toISOString().replace(".000Z","Z");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
function table(rows) {
  if (!rows.length) return el("div", "none", "empty");
  const cols = Array.from(new Set(rows.flatMap(r => Object.keys(r))));
  const t = el("table"); const h = el("tr"); cols.forEach(c => h.appendChild(el("th", c))); t.appendChild(h);
  rows.forEach(r => { const tr = el("tr"); cols.forEach(c => tr.appendChild(el("td", fmt(r[c])))); t.appendChild(tr); });
  return t;
}
function render(value) {
  if (Array.isArray(value)) return value.length && typeof value[0] === "object" ? table(value) : el("div", fmt(value));
  if (value && typeof value === "object") {
    const t = el("table", undefined, "kv");
    Object.entries(value).forEach(([k, v]) => {
      const tr = el("tr"); tr.appendChild(el("td", k)); const td = el("td");
      if (v && typeof v === "object" && Object.keys(v).length) td.appendChild(render(v)); else td.textContent = fmt(v);
      tr.appendChild(td); t.appendChild(tr);
    });
    return t;
  }
  return el("div", fmt(value));
}
function paint(data) {
  const main = document.getElementById("panels"); main.replaceChildren();
  if (data.status === "UNAVAILABLE") { main.appendChild(el("section", data.detail, "bad")); return; }
  const p = data.panels || {};
  const ov = p.overview || {};
  const hs = document.getElementById("health"); hs.textContent = "health: " + fmt(ov.health);
  hs.className = "pill " + (ov.health === "HEALTHY" ? "ok" : ov.health === "CRITICAL" ? "bad" : "warn");
  const ks = (ov.kill_switch || {}); const k = document.getElementById("kill");
  k.textContent = "kill switch: " + fmt(ks.status); k.className = "pill " + (ks.blocks_new_entries ? "warn" : "ok");
  const rt = (ov.runtime || {}); const r = document.getElementById("runtime");
  r.textContent = "runtime: " + fmt(rt.status) + (rt.mode ? " (" + rt.mode + ")" : "");
  r.className = "pill " + (rt.status === "RUNNING" ? "ok" : "warn");
  ORDER.filter(n => n in p).forEach(name => {
    const s = el("section"); s.appendChild(el("h2", name));
    const body = Object.assign({}, p[name]);
    if (body.status === "UNAVAILABLE" || body.status === "NO_DATA") s.appendChild(el("div", body.status + ": " + body.detail, "warn"));
    else { delete body.status; s.appendChild(render(body)); }
    main.appendChild(s);
  });
}
let pollTimer = null;
async function poll() {
  try { const res = await fetch("/api/panels"); paint(await res.json());
        document.getElementById("conn").textContent = "polling \\u00b7 " + new Date().toLocaleTimeString(); }
  catch (e) { document.getElementById("conn").textContent = "dashboard server unreachable"; }
}
function startPolling() { if (!pollTimer) { poll(); pollTimer = setInterval(poll, 3000); } }
function connect() {
  let ws;
  try { ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws"); }
  catch (e) { startPolling(); return; }
  ws.onmessage = ev => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    paint(JSON.parse(ev.data)); document.getElementById("conn").textContent = "live \\u00b7 " + new Date().toLocaleTimeString(); };
  ws.onclose = () => { startPolling(); setTimeout(connect, 5000); };
  ws.onerror = () => ws.close();
}
connect();
</script>
</body>
</html>
"""
