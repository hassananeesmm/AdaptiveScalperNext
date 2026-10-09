"""Local, dependency-free, observer-only Windows dashboard.

No outside assets or dynamic HTML insertion. All broker-derived values are
snapshots written by the runtime; this browser never opens MT5 or writes to
the database. Designed for Windows laptop sizes, scaling and touch input.
"""

PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<link rel="icon" href="data:,">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>AdaptiveScalperNext · Monitor</title>
<style>
:root {
  color-scheme:dark;
  --bg:#0a101b;--side:#0f1929;--surface:#142034;--surface-2:#1b2b42;
  --text:#e7eef9;--muted:#a4b4cb;--line:#30435f;--accent:#76baff;
  --good:#6ddd9d;--warn:#f8c46d;--bad:#ff8a90;--radius:14px;
  font-family:Segoe UI,system-ui,-apple-system,sans-serif;font-size:14px;
}
:root[data-theme="light"] {
  color-scheme:light;--bg:#f2f5fa;--side:#e6eef8;--surface:#ffffff;
  --surface-2:#f4f8fd;--text:#1b293e;--muted:#536783;--line:#d7e1ed;
  --accent:#125fa7;--good:#146b3b;--warn:#95600a;--bad:#b42332;
}
*{box-sizing:border-box} html{height:100%}body{margin:0;min-height:100%;background:var(--bg);color:var(--text)}
button,input,select{font:inherit;color:inherit;background:var(--surface);border:1px solid var(--line);
  border-radius:9px;min-height:34px}button{cursor:pointer}select{padding:0 8px}
button:focus-visible,input:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.shell{min-height:100vh;display:grid;grid-template-columns:222px minmax(0,1fr)}
.sidebar{background:var(--side);border-right:1px solid var(--line);padding:18px 12px;position:sticky;top:0;height:100vh;overflow-y:auto;overflow-x:hidden}
.brand{display:flex;gap:11px;align-items:center;padding:5px 8px 20px;min-width:0}
.brand>div:not(.logo){min-width:0}
.logo{background:linear-gradient(135deg,#2c81c7,#75c5db);color:#061629;font-weight:900;
  width:36px;height:36px;border-radius:11px;display:grid;place-items:center}
.brand-title{font-size:15px;font-weight:800;letter-spacing:.01em;line-height:1.2;overflow-wrap:break-word}
.brand-sub{font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nav-label{font-size:11px;letter-spacing:.1em;font-weight:750;color:var(--muted);
  text-transform:uppercase;margin:12px 12px 7px}
.nav{display:grid;gap:4px}
.nav button{color:var(--muted);border:1px solid transparent;background:transparent;
  text-align:left;padding:10px 12px;border-radius:9px;min-height:42px;font-weight:600}
.nav button:hover{background:var(--surface-2);color:var(--text)}
.nav button[aria-current="page"]{color:var(--text);background:var(--surface-2);
  border-color:var(--line);box-shadow:inset 3px 0 var(--accent)}
.side-foot{color:var(--muted);font-size:12px;margin:22px 9px 8px;line-height:1.7}
.workspace{min-width:0}
.topbar{position:sticky;top:0;z-index:10;background:var(--bg);
  border-bottom:1px solid var(--line);padding:13px clamp(14px,2.3vw,28px);
  display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.top-title{font-weight:800;margin-right:auto;font-size:16px}
.pill{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--line);
  padding:5px 10px;border-radius:999px;font-size:12px;font-weight:700;background:var(--surface)}
.pill.good{color:var(--good)}.pill.warn{color:var(--warn)}.pill.bad{color:var(--bad)}
.tiny{color:var(--muted);font-size:12px}
.secondary-button{color:var(--text);background:var(--surface);border:1px solid var(--line);
  padding:8px 11px;border-radius:9px;min-height:36px}
.secondary-button:hover{border-color:var(--accent)}
.content{padding:22px clamp(14px,2.3vw,28px) 40px;min-width:0;max-width:2000px;margin:auto}
.hero{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:flex-end;margin-bottom:18px}
.hero h1{font-size:clamp(22px,2.5vw,32px);letter-spacing:-.025em;margin:0 0 7px}
.hero p{margin:0;color:var(--muted);max-width:75ch;line-height:1.5}
.clock{color:var(--muted);font-size:12px;white-space:nowrap}
.warning-banner{background:color-mix(in srgb,var(--bad) 12%,var(--surface));
  border:1px solid var(--bad);border-radius:10px;padding:11px 14px;margin:0 0 14px;
  color:var(--text);display:none;overflow-wrap:anywhere}
.warning-banner.visible{display:block}
.kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:16px 0 24px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  min-width:0;padding:14px 15px;min-height:94px}
.kpi .label{color:var(--muted);font-size:12px;display:block;margin-bottom:10px}
.kpi .value{font-size:clamp(16px,1.8vw,24px);font-weight:790;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}
.kpi .hint{font-size:11px;color:var(--muted);margin-top:5px;line-height:1.35}
.toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:12px 0 14px}
.toolbar h2{font-size:17px;margin:0 auto 0 0}
.search{width:min(360px,100%);min-height:38px;color:var(--text);background:var(--surface);
  border:1px solid var(--line);padding:9px 12px;border-radius:10px}
.panel-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start}
.panel{min-width:0;background:var(--surface);border:1px solid var(--line);
  border-radius:var(--radius);overflow:hidden}
.panel.wide{grid-column:1/-1}
.panel-head{display:flex;align-items:center;gap:8px;padding:13px 15px;
  border-bottom:1px solid var(--line);background:var(--surface-2)}
.panel-head h3{font-size:14px;margin:0;font-weight:750}
.panel-head .source{color:var(--muted);font-size:11px;margin-left:auto;text-align:right}
.panel-body{min-width:0;padding:13px 15px;max-height:min(62vh,650px);overflow:auto;
  scrollbar-color:var(--line) transparent}
.panel-body:focus{outline:2px solid var(--accent);outline-offset:-2px}
.panel.wide .panel-body{max-height:min(70vh,750px)}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:10px;margin-bottom:12px}
.stat{background:var(--surface-2);border:1px solid var(--line);border-radius:9px;padding:10px;min-width:0}
.stat .label{color:var(--muted);font-size:11px}.stat .value{font-size:17px;font-weight:750;margin-top:5px;
  overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.table-scroll{width:100%;max-width:100%;overflow:auto;border:1px solid var(--line);border-radius:8px;margin:8px 0}
table{width:100%;min-width:480px;border-collapse:collapse;font-size:12px}
th,td{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:700;background:var(--surface-2);position:sticky;top:0}
tr:last-child td{border-bottom:0}
td{max-width:280px;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.kv{width:100%;min-width:0}
.kv th,.kv td{background:none;position:static}
.kv td:first-child{width:33%;color:var(--muted);min-width:100px}
details{border-top:1px solid var(--line);padding:8px 0}
details:first-child{border-top:0}
summary{cursor:pointer;font-weight:680;color:var(--text)}
.nested{padding:9px 0 1px;min-width:0}
.empty{color:var(--muted);padding:12px 2px;font-size:13px;line-height:1.5}
.error{color:var(--bad);overflow-wrap:anywhere}
.text-good{color:var(--good)}.text-bad{color:var(--bad)}.text-warn{color:var(--warn)}
.safeguard{border:1px solid var(--line);border-radius:9px;padding:10px;margin-top:12px;
  font-size:12px;color:var(--muted)}
.bars{display:flex;align-items:flex-end;gap:3px;min-height:116px;padding:13px 0 6px;overflow-x:auto}
.bar-col{display:flex;flex-direction:column;align-items:center;justify-content:flex-end;
  width:25px;flex:none;height:100px}
.bar{width:14px;min-height:2px;background:var(--good);border-radius:3px 3px 0 0}
.bar.negative{background:var(--bad)}
.bar-day{font-size:9px;color:var(--muted);margin-top:5px}
.mini-title{font-size:13px;font-weight:750;margin:14px 0 7px}
.footer{color:var(--muted);font-size:12px;border-top:1px solid var(--line);margin-top:26px;
  padding-top:13px;line-height:1.6}
.hidden{display:none!important}
/* ---- Strategy Lab ---- */
:root{--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--pos:#3987e5;--neg:#e66767}
:root[data-theme="light"]{--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--pos:#2a78d6;--neg:#e34948}
.lab{display:grid;gap:14px;min-width:0}
.lab-bar{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;min-width:0}
.lab-bar label{display:flex;flex-direction:column;gap:3px;font-size:11px;color:var(--muted);min-width:0}
.lab-bar select,.lab-bar input{min-width:0;max-width:220px;padding:0 8px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;flex-wrap:wrap;max-width:100%}
.seg button{border:0;border-radius:0;background:var(--surface);padding:7px 13px;font-weight:650;color:var(--muted)}
.seg button[aria-selected="true"]{background:var(--surface-2);color:var(--text);box-shadow:inset 0 -3px var(--accent)}
.lab-note{font-size:12px;color:var(--muted);line-height:1.5}
.num{text-align:right;white-space:nowrap}
.pos{color:var(--good)}.neg{color:var(--bad)}.na{color:var(--muted)}
.linkish{background:none;border:0;color:var(--accent);padding:0;min-height:0;text-align:left;font-weight:650;text-decoration:underline;white-space:nowrap}.lab td{max-width:none}.nowrap-table td{white-space:nowrap}
tr.clickable{cursor:pointer}tr.clickable:hover td,tr.clickable:focus td{background:var(--surface-2)}
tr.clickable:focus{outline:2px solid var(--accent);outline-offset:-2px}
.chart-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px;min-width:0}
.chart-card h4{margin:0 0 6px;font-size:13px}
.chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.chart svg{width:100%;height:auto;display:block}
.chart .axis{stroke:var(--line);stroke-width:1}.chart text{fill:var(--muted);font-size:10px}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;margin-top:6px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.swatch{width:12px;height:3px;border-radius:2px;display:inline-block}
.tip{position:fixed;z-index:50;pointer-events:none;background:var(--surface-2);border:1px solid var(--line);
  border-radius:8px;padding:6px 9px;font-size:12px;color:var(--text);box-shadow:0 4px 14px rgba(0,0,0,.3);display:none}
pre.src{background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;
  font-size:12px;max-height:360px;white-space:pre}
.pager{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0}
.badge{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:1px 8px;font-size:11px;font-weight:700}
.badge.ok{color:var(--good)}.badge.bad{color:var(--bad)}
@media(max-width:1080px){.chart-grid{grid-template-columns:minmax(0,1fr)}}
@media(max-width:1400px){.kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:1080px){.shell{grid-template-columns:180px minmax(0,1fr)}
  .sidebar{padding:12px 7px}.panel-grid{grid-template-columns:minmax(0,1fr)}
  .panel.wide{grid-column:auto}}
@media(max-width:740px){.shell{display:block}.sidebar{position:static;height:auto;border-right:0;
  border-bottom:1px solid var(--line);padding:9px 12px}
  .brand{padding:4px 0 7px}.side-foot,.nav-label{display:none}
  .nav{display:flex;overflow-x:auto;padding-bottom:4px;gap:6px;scrollbar-width:thin}
  .nav button{white-space:nowrap;min-height:38px;padding:7px 11px}
  .topbar{position:static}.top-title{width:100%}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
  .toolbar h2{flex-basis:100%}.search{flex:1;width:auto}
  .content{padding:15px 12px 30px}}
@media(max-width:400px){.kpis{grid-template-columns:minmax(0,1fr)}
  .stat-grid{grid-template-columns:minmax(0,1fr)}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style>
</head>
<body>
<div class="shell">
  <aside class="sidebar" aria-label="Dashboard navigation">
    <div class="brand"><div class="logo" aria-hidden="true">AS</div>
      <div><div class="brand-title">AdaptiveScalper<wbr>Next</div><div class="brand-sub">Local operations monitor</div></div>
    </div>
    <div class="nav-label">Views</div><nav class="nav" id="nav" aria-label="Views"></nav>
    <div class="side-foot">Observer only<br>MT5 DEMO / PAPER<br>Broker orders cannot be sent here.</div>
  </aside>
  <div class="workspace">
    <header class="topbar">
      <div class="top-title">Operations dashboard</div>
      <span class="pill bad">REAL-MONEY EXECUTION: DISABLED</span>
      <span class="pill warn" id="health">Health: awaiting data</span>
      <span class="pill warn" id="runtime">Runtime: awaiting data</span>
      <span class="pill warn" id="kill">Kill switch: unknown</span>
      <span class="pill warn" id="feed" role="status" aria-live="polite">Connecting…</span>
      <button type="button" class="secondary-button" id="refresh" title="Fetch the latest stored runtime snapshot">Refresh</button>
      <button type="button" class="secondary-button" id="theme" title="Switch light or dark appearance">Theme</button>
    </header>
    <div class="content">
      <div class="hero">
        <div><h1 id="view-title">Overview</h1>
          <p id="view-desc">Latest available broker snapshots, risk evidence and operational health. This page cannot control trading.</p></div>
        <div class="clock" id="clock">Awaiting first update</div>
      </div>
      <div id="alert" class="warning-banner" role="alert"></div>
      <div class="kpis" id="kpis" aria-label="Key monitoring figures"></div>
      <div class="toolbar" id="panel-toolbar">
        <h2 id="section-title">Live overview</h2>
        <input class="search" type="search" id="search" placeholder="Find a panel…" aria-label="Find a panel">
        <span class="tiny" id="data-age">No data</span>
      </div>
      <main class="panel-grid" id="panels" aria-live="off"></main>
      <section class="lab hidden" id="lab" aria-label="Strategy Lab"></section>
      <div class="tip" id="tip" role="tooltip"></div><svg id="svg-ns" class="hidden" aria-hidden="true"></svg>
      <div class="footer">Live means the local runtime reported fresh data. A browser connection alone does not prove MT5 is connected. Account values and positions are snapshots, not a direct connection to the broker. No trading or kill-switch controls are exposed here.</div>
    </div>
  </div>
</div>
<script>
"use strict";
const GROUPS = [
  {id:"overview", label:"Overview", desc:"Account, exposure, active orders and market health.",
   panels:["market","positions","orders","performance","decisions","overview"]},
  {id:"markets",label:"Markets",desc:"Broker quotes, symbol resolution and decision context.",
   panels:["market","symbols","microstructure","news","history"]},
  {id:"trading",label:"Trading",desc:"Positions, broker orders, simulated results and decision journal.",
   panels:["positions","orders","decisions","performance","costs"]},
  {id:"safety",label:"Safety & risk",desc:"Kill switch, active incidents, risk exposure and news health.",
   panels:["overview","multi_position","risk","orders","events","news"]},
  {id:"research",label:"Research",desc:"Historical evaluations, observed outcomes, models and knowledge.",
   panels:["performance","research","learning","memory","knowledge","history"]},
  {id:"system",label:"System",desc:"Component status, runtime events, data freshness and broker status.",
   panels:["overview","components","market","events","history"]},
  {id:"strategy_lab",label:"Strategy Lab",desc:"Which strategy generated, submitted, filled and closed every trade; "+
   "winning and losing strategies with recorded costs; DEMO, PAPER and BACKTEST kept separate; every trade traceable. "+
   "Review-only: no strategy activation, auto-promotion or risk change is exposed here.",
   panels:["strategy_registry","strategy_activity","strategy_performance","strategy_attribution"]},
  {id:"all",label:"All panels",desc:"Every implemented dashboard panel.",
   panels:["overview","market","performance","components","symbols","positions","orders","decisions",
           "risk","news","events","costs","research","learning","memory","knowledge","history",
           "strategy_registry","strategy_activity","strategy_performance","strategy_attribution","multi_position",
           "microstructure"]}
];
const WIDE = new Set(["market","positions","orders","performance","decisions","events","history",
  "strategy_registry","strategy_activity","strategy_performance","strategy_attribution","multi_position"]);
const FRIENDLY = {
  overview:"System overview",market:"Live market & account",performance:"Observed performance",
  components:"Components",symbols:"Symbol resolution",positions:"Positions",orders:"Orders & reconciliation",
  decisions:"Decisions & reasons",risk:"Risk evidence",news:"Economic news",events:"Incidents & events",
  costs:"Execution costs",research:"Research history",learning:"Model observer",
  memory:"RAG memory",knowledge:"Knowledge bundle",history:"Historical coverage",
  strategy_registry:"Strategy overview",strategy_activity:"Live strategy activity",
  strategy_performance:"Performance comparison",strategy_attribution:"Broker-verified attribution",
  multi_position:"MULTI-POSITION READINESS",microstructure:"Microstructure (observation only)"
};
const el = (tag, text, cls) => {
  const n=document.createElement(tag);
  if(text!==undefined && text!==null)n.textContent=String(text);
  if(cls)n.className=cls;
  return n;
};
const money=(n,currency) => {
  if(typeof n!=="number"||!Number.isFinite(n))return "—";
  return new Intl.NumberFormat(undefined,{style:"decimal",minimumFractionDigits:2,
    maximumFractionDigits:2}).format(n)+(currency?" "+currency:"");
};
const fmt=(v) => {
  if(v===undefined||v===null||v==="")return "—";
  if(typeof v==="boolean")return v?"Yes":"No";
  if(typeof v==="number")return Number.isFinite(v)?v.toLocaleString(undefined,{maximumFractionDigits:6}):"—";
  if(Array.isArray(v))return v.length?String(v.length)+" records":"None";
  if(typeof v==="object")return JSON.stringify(v);
  return String(v);
};
const time=(n) => {
  if(!Number.isFinite(Number(n))||!n)return "—";
  return new Date(Number(n)*1000).toLocaleString(undefined,{dateStyle:"medium",timeStyle:"medium"});
};
const heading=(key)=>String(key).replace(/_/g," ").replace(/\b\w/g,c=>c.toUpperCase());
function table(rows){
  if(!rows||!rows.length)return el("p","No recorded data for this period.","empty");
  const cols=Array.from(new Set(rows.flatMap(r=>Object.keys(r||{}))));
  const scroll=el("div",null,"table-scroll");
  const t=el("table");const head=el("thead");const hr=el("tr");
  cols.forEach(c=>hr.appendChild(el("th",heading(c))));head.appendChild(hr);t.appendChild(head);
  const body=el("tbody");
  rows.forEach(r=>{
    const tr=el("tr");
    cols.forEach(c=>{const value=r?r[c]:null;const td=el("td",
      /(_utc|_at|_time)$/.test(c)&&typeof value==="number"?time(value):fmt(value));
      if(value!==null&&value!==undefined)td.title=typeof value==="object"?JSON.stringify(value):String(value);
      tr.appendChild(td);
    });body.appendChild(tr);
  });t.appendChild(body);scroll.appendChild(t);return scroll;
}
function render(value,depth){
  if(depth>6)return el("span","More detail available in the source data.","empty");
  if(Array.isArray(value)){
    if(!value.length)return el("p","No records yet.","empty");
    if(value.every(v=>v&&typeof v==="object"&&!Array.isArray(v)))return table(value);
    const wrap=el("div");
    value.forEach(v=>wrap.appendChild(render(v,depth+1)));return wrap;
  }
  if(value&&typeof value==="object"){
    const wrap=el("div");
    Object.entries(value).forEach(([key,val])=>{
      if(val&&typeof val==="object"){
        const detail=el("details");
        if(depth===0)detail.open=true;
        detail.appendChild(el("summary",heading(key)));
        const nested=el("div",null,"nested");nested.appendChild(render(val,depth+1));
        detail.appendChild(nested);wrap.appendChild(detail);
      }else{
        const t=el("table",null,"kv");const tr=el("tr");
        tr.appendChild(el("td",heading(key)));
        const td=el("td",/(_utc|_at|_time)$/.test(key)&&typeof val==="number"?time(val):fmt(val));
        tr.appendChild(td);t.appendChild(tr);wrap.appendChild(t);
      }
    });return wrap;
  }
  return el("span",fmt(value));
}
function stat(label,value,context){
  const card=el("div",null,"stat");card.appendChild(el("div",label,"label"));
  card.appendChild(el("div",value,"value"));if(context)card.appendChild(el("div",context,"tiny"));return card;
}
function marketPanel(panel){
  const wrap=el("div");const acct=panel.account||{};const terminal=panel.terminal||{};
  const stats=el("div",null,"stat-grid");
  stats.appendChild(stat("Broker",fmt(acct.company),"Last sampled"));
  stats.appendChild(stat("Account mode",fmt(acct.trade_mode),panel.status==="OK"?"Fresh snapshot":"STALE / UNAVAILABLE"));
  stats.appendChild(stat("MT5 connection",terminal.connected?"Connected":"Not verified",fmt(terminal.build?"Build "+terminal.build:null)));
  stats.appendChild(stat("Data sampled",time(panel.sampled_at_utc),
    panel.age_seconds===null||panel.age_seconds===undefined?"":fmt(panel.age_seconds)+" seconds ago"));
  wrap.appendChild(stats);
  const quotes=Object.entries(panel.quotes||{}).map(([symbol,v])=>({
    symbol: symbol,broker_symbol:v.broker_symbol||"—",bid:v.bid??null,ask:v.ask??null,
    spread_points:v.spread_points??null,status:v.status==="FRESH"?"LIVE FEED":(v.status||"UNAVAILABLE"),
    quote_age_seconds:v.quote_age_seconds??null,time_utc:v.time_utc??null
  }));
  wrap.appendChild(el("div","Sampled broker quotes","mini-title"));wrap.appendChild(table(quotes));
  if(panel.broker_positions!==null&&panel.broker_positions!==undefined){
    wrap.appendChild(el("div","Current broker positions (DEMO only)","mini-title"));
    wrap.appendChild(table(panel.broker_positions));
  }
  if(panel.errors&&panel.errors.length)wrap.appendChild(el("p",panel.errors.join(" · "),"error"));
  const clock=panel.server_clock||{};
  wrap.appendChild(el("div","MT5 time basis: "+fmt(clock.verdict)+" · "+fmt(clock.rule),"safeguard"));
  return wrap;
}
function performancePanel(panel){
  const wrap=el("div");const demo=panel.demo||{};const paper=panel.paper||{};
  const stats=el("div",null,"stat-grid");
  stats.appendChild(stat("DEMO booked net (30d)",money(demo.net),"Observed deals; not equity"));
  stats.appendChild(stat("Observed DEMO deals",fmt(demo.observed_deals),"Not a trade win rate"));
  stats.appendChild(stat("PAPER symbols",fmt((paper.by_symbol||[]).length),"Independent simulated sessions"));
  wrap.appendChild(stats);
  if((demo.daily_net||[]).length){
    wrap.appendChild(el("div","Observed daily DEMO booked net · last 30 days","mini-title"));
    const series=demo.daily_net,max=Math.max(0.01,...series.map(x=>Math.abs(x.booked_net||0)));
    const chart=el("div",null,"bars");
    chart.setAttribute("aria-label","Daily observed DEMO booked net, visual bars. Exact values in table.");
    series.forEach(x=>{
      const col=el("div",null,"bar-col");col.title=x.day_utc+": "+money(x.booked_net);
      const bar=el("div",null,"bar"+(x.booked_net<0?" negative":""));
      bar.style.height=String(Math.max(2,Math.round(Math.abs(x.booked_net||0)/max*75)))+"px";
      col.appendChild(bar);col.appendChild(el("span",String(x.day_utc).slice(5),"bar-day"));chart.appendChild(col);
    });
    wrap.appendChild(chart);wrap.appendChild(table(series));
  }else wrap.appendChild(el("p","No observed DEMO deals in this period.","empty"));
  wrap.appendChild(el("div","PAPER closed trades by symbol (never combined with DEMO)","mini-title"));
  wrap.appendChild(table(paper.by_symbol||[]));
  wrap.appendChild(el("div","Recent PAPER closed trades","mini-title"));
  wrap.appendChild(table(paper.recent_closed_trades||[]));
  wrap.appendChild(el("div","Research outcomes and simulated fills are not evidence of a validated trading edge.","safeguard"));
  return wrap;
}
function round2(n){return Math.round((n+Number.EPSILON)*100)/100;}
function csv(rows){
  if(!rows.length)return "";
  const cols=Array.from(new Set(rows.flatMap(r=>Object.keys(r))));
  const esc=v=>{const s=v===null||v===undefined?"":String(v);return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};
  return [cols.join(","),...rows.map(r=>cols.map(c=>esc(r[c])).join(","))].join("\n");
}
function downloadText(filename,text,mime){
  const blob=new Blob([text],{type:mime||"application/json"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=filename;
  document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}
function stratRegistryPanel(panel){
  const wrap=el("div");
  wrap.appendChild(el("p","Source-verified from the live strategy registry ("+
    fmt(panel.window_days)+"-day evidence window). A PROPOSED strategy has not been executed.","tiny"));
  const wkey="counts_last_"+panel.window_days+"d";
  const summary=(panel.strategies||[]).map(s=>({
    strategy_key:s.strategy_key,status:s.registration_status,stage:s.lifecycle_stage,version:s.version,
    signals:(s[wkey]||{}).signals_created??0,rejected:(s[wkey]||{}).signals_rejected??0,
    selected:(s[wkey]||{}).proposals_selected??0,allowed:(s[wkey]||{}).entries_allowed??0,
    blocked:(s[wkey]||{}).entries_blocked??0,
    last_signal:s.last_genuine_signal?time(s.last_genuine_signal.event_timestamp_utc):"—",
    last_position:s.last_position?(s.last_position.status+" "+s.last_position.canonical_symbol+" "+s.last_position.direction):"—"
  }));
  wrap.appendChild(table(summary));
  wrap.appendChild(el("div","Per-strategy detail (source-verified entry conditions, regimes, stop/target logic and default parameters)","mini-title"));
  (panel.strategies||[]).forEach(s=>{
    const d=el("details");
    d.appendChild(el("summary",s.strategy_key+" — v"+fmt(s.version)+" · "+s.registration_status+" · "+s.lifecycle_stage));
    const body=el("div",null,"nested");
    if(s.source_description)body.appendChild(el("p",s.source_description));
    if(s.default_parameters){
      body.appendChild(el("div","Default parameters (stop/target/confidence/duration logic lives here)","mini-title"));
      body.appendChild(render(s.default_parameters,1));
    }
    body.appendChild(el("div","Latest recorded evidence","mini-title"));
    body.appendChild(render({
      last_genuine_signal:s.last_genuine_signal,last_selection:s.last_selection,
      last_entry_allowed:s.last_entry_allowed,last_entry_blocked:s.last_entry_blocked,
      last_position:s.last_position
    },1));
    d.appendChild(body);wrap.appendChild(d);
  });
  return wrap;
}
function stratActivityPanel(panel){
  const wrap=el("div");const rows=panel.recent_evaluations||[];
  wrap.appendChild(el("p","Latest "+rows.length+" recorded entry decisions, real database evidence only ("+
    "entry_decisions). The reason shown is the complete recorded explanation.","tiny"));
  const flat=rows.map(r=>({
    decided_at_utc:r.decided_at_utc,mode:r.mode,symbol:r.canonical_symbol,bar_time_utc:r.bar_time_utc,
    strategy_key:r.strategy_key,direction:r.direction,stage:r.stage,decision:r.decision,reason:r.reason
  }));
  wrap.appendChild(table(flat));
  return wrap;
}
function stratAttributionPanel(panel){
  const wrap=el("div");const rows=panel.attributed_positions||[];
  wrap.appendChild(el("p",fmt(rows.length)+" broker-verified DEMO position(s), each deal summed exactly once. "+
    "Partial fills and multiple closing deals are accounted for, never guessed.","tiny"));
  wrap.appendChild(table(rows.map(r=>({
    broker_position_id:r.broker_position_id,symbol:r.canonical_symbol,strategy_key:r.strategy_key,
    direction:r.direction,volume:r.volume,status:r.status,deal_count:r.deal_count,
    partial_fill_or_multi_close:r.partial_fill_or_multi_close,gross_pnl:money(r.gross_pnl),
    costs:money(r.costs),net_pnl:money(r.net_pnl),opened_at_utc:r.opened_at_utc,closed_at_utc:r.closed_at_utc
  }))));
  wrap.appendChild(el("div","Trade drilldown (broker order/deal references, prices, timestamps, costs)","mini-title"));
  rows.forEach(r=>{
    const d=el("details");
    d.appendChild(el("summary",fmt(r.canonical_symbol)+" "+fmt(r.direction)+" · position "+fmt(r.broker_position_id)+
      " · "+fmt(r.strategy_key)));
    const body=el("div",null,"nested");body.appendChild(table(r.deals||[]));
    d.appendChild(body);wrap.appendChild(d);
  });
  const un=panel.unattributed_deals||[];
  wrap.appendChild(el("div","UNATTRIBUTED broker deals ("+fmt(un.length)+") — no matching local position; never guessed onto a strategy","mini-title"));
  wrap.appendChild(table(un));
  return wrap;
}
const STRAT_PERF_STATE={tab:"demo",filters:{strategy:"",symbol:"",direction:"",regime:"",version:"",provenance:""},
  from:"",to:"",shortlist:(()=>{try{return JSON.parse(localStorage.getItem("asn-strategy-shortlist")||"[]");}
  catch(e){return [];}})()};
function normTrade(tab,t){
  if(tab==="demo")return{strategy_key:t.strategy_key,symbol:t.canonical_symbol,direction:t.direction,
    regime:null,version:null,provenance:null,net:t.net_pnl,gross:t.gross_pnl,cost:-(t.costs||0),
    entry_time:t.opened_at_utc,exit_time:t.closed_at_utc,realized_r:null,exit_reason:null,raw:t};
  return{strategy_key:t.strategy_key,symbol:t.canonical_symbol,direction:t.direction,
    regime:t.exit_regime||t.entry_regime,version:t.strategy_version,provenance:t.cost_provenance,
    net:t.realized_pnl,gross:t.gross_pnl,cost:t.total_cost,entry_time:t.entry_time_utc,exit_time:t.exit_time_utc,
    realized_r:t.realized_r,exit_reason:t.exit_reason,raw:t};
}
function aggregateTrades(rows){
  const groups={};rows.forEach(r=>{(groups[r.strategy_key||"UNKNOWN"]=groups[r.strategy_key||"UNKNOWN"]||[]).push(r);});
  return Object.entries(groups).map(([key,trs])=>{
    const sorted=trs.slice().sort((a,b)=>(a.exit_time||0)-(b.exit_time||0));
    const net=sorted.map(t=>t.net||0);
    const wins=net.filter(x=>x>1e-9).length,losses=net.filter(x=>x<-1e-9).length;
    const grossProfit=net.filter(x=>x>0).reduce((a,b)=>a+b,0);
    const grossLoss=Math.abs(net.filter(x=>x<0).reduce((a,b)=>a+b,0));
    let running=0,peak=0,maxDD=0;net.forEach(x=>{running+=x;peak=Math.max(peak,running);maxDD=Math.max(maxDD,peak-running);});
    const rVals=sorted.map(t=>t.realized_r).filter(x=>typeof x==="number");
    const holds=sorted.map(t=>(typeof t.entry_time==="number"&&typeof t.exit_time==="number")?t.exit_time-t.entry_time:null)
      .filter(x=>x!==null);
    return{strategy_key:key,sample_size:sorted.length,wins,losses,breakeven:sorted.length-wins-losses,
      gross_pnl:round2(sorted.reduce((a,t)=>a+(t.gross||0),0)),cost:round2(sorted.reduce((a,t)=>a+(t.cost||0),0)),
      net_pnl:round2(net.reduce((a,b)=>a+b,0)),
      win_rate:sorted.length?round2(100*wins/sorted.length):null,
      profit_factor:grossLoss>0?round2(grossProfit/grossLoss):(grossProfit>0?Infinity:null),
      avg_r:rVals.length?round2(rVals.reduce((a,b)=>a+b,0)/rVals.length):null,
      total_r:rVals.length?round2(rVals.reduce((a,b)=>a+b,0)):null,
      avg_holding_seconds:holds.length?Math.round(holds.reduce((a,b)=>a+b,0)/holds.length):null,
      max_drawdown:sorted.length?round2(maxDD):null,trades:sorted};
  }).sort((a,b)=>b.sample_size-a.sample_size);
}
function stratPerformancePanel(panel){
  const wrap=el("div");const S=STRAT_PERF_STATE;
  const tabsRow=el("div",null,"toolbar");
  ["demo","paper","backtest"].forEach(t=>{
    const b=el("button",t.toUpperCase());b.type="button";b.className="secondary-button";
    if(S.tab===t)b.style.borderColor="var(--accent)";
    b.addEventListener("click",()=>{S.tab=t;lastContent["strategy_performance"]=null;paint(data);});
    tabsRow.appendChild(b);
  });
  wrap.appendChild(tabsRow);
  const allRows=(panel[S.tab+(S.tab==="demo"?"_closed_trades":"_trades")]||[]).map(t=>normTrade(S.tab,t));
  wrap.appendChild(el("p","Evidence source: "+S.tab.toUpperCase()+" · "+allRows.length+
    " row(s) shipped for this tab. DEMO, PAPER and BACKTEST are never pooled.","tiny"));
  const distinct=(f)=>Array.from(new Set(allRows.map(f).filter(v=>v!==null&&v!==undefined&&v!==""))).sort();
  const filterRow=el("div",null,"toolbar");
  const makeSelect=(label,key,options)=>{
    const s=el("select");const allOpt=el("option","All "+label);allOpt.value="";s.appendChild(allOpt);
    options.forEach(o=>{const opt=el("option",String(o));opt.value=String(o);s.appendChild(opt);});
    s.value=S.filters[key]||"";
    s.addEventListener("change",()=>{S.filters[key]=s.value;lastContent["strategy_performance"]=null;paint(data);});
    filterRow.appendChild(s);return s;
  };
  makeSelect("strategies","strategy",distinct(r=>r.strategy_key));
  makeSelect("symbols","symbol",distinct(r=>r.symbol));
  makeSelect("directions","direction",distinct(r=>r.direction));
  makeSelect("regimes","regime",distinct(r=>r.regime));
  makeSelect("versions","version",distinct(r=>r.version));
  makeSelect("provenance","provenance",distinct(r=>r.provenance));
  const fromInput=el("input");fromInput.type="date";fromInput.value=S.from;
  fromInput.addEventListener("change",()=>{S.from=fromInput.value;lastContent["strategy_performance"]=null;paint(data);});
  const toInput=el("input");toInput.type="date";toInput.value=S.to;
  toInput.addEventListener("change",()=>{S.to=toInput.value;lastContent["strategy_performance"]=null;paint(data);});
  filterRow.appendChild(fromInput);filterRow.appendChild(toInput);
  wrap.appendChild(filterRow);
  const fromTs=S.from?Date.parse(S.from)/1000:null,toTs=S.to?Date.parse(S.to)/1000+86400:null;
  const filtered=allRows.filter(r=>
    (!S.filters.strategy||r.strategy_key===S.filters.strategy)&&
    (!S.filters.symbol||r.symbol===S.filters.symbol)&&
    (!S.filters.direction||r.direction===S.filters.direction)&&
    (!S.filters.regime||r.regime===S.filters.regime)&&
    (!S.filters.version||String(r.version)===S.filters.version)&&
    (!S.filters.provenance||r.provenance===S.filters.provenance)&&
    (fromTs===null||(r.exit_time||0)>=fromTs)&&(toTs===null||(r.exit_time||0)<toTs)
  );
  const agg=aggregateTrades(filtered);
  if(!agg.length){wrap.appendChild(el("p","No trades match the current filters for this tab.","empty"));return wrap;}
  const table1=el("div",null,"table-scroll");const t1=el("table");
  const head=el("thead");const hr=el("tr");
  ["Compare","Strategy","N","Wins","Losses","BE","Win rate","Profit factor","Gross P&L","Costs","Net P&L",
    "Avg R","Total R","Avg hold","Max DD"].forEach(h=>hr.appendChild(el("th",h)));
  head.appendChild(hr);t1.appendChild(head);const body=el("tbody");
  agg.forEach(a=>{
    const tr=el("tr");
    const cb=document.createElement("input");cb.type="checkbox";
    const shortlistKey=S.tab+":"+a.strategy_key;
    cb.checked=S.shortlist.includes(shortlistKey);
    cb.addEventListener("change",()=>{
      if(cb.checked){
        if(S.shortlist.length>=3){cb.checked=false;return;}
        S.shortlist.push(shortlistKey);
      }else{S.shortlist=S.shortlist.filter(k=>k!==shortlistKey);}
      try{localStorage.setItem("asn-strategy-shortlist",JSON.stringify(S.shortlist));}catch(e){}
      lastContent["strategy_performance"]=null;paint(data);
    });
    const cbTd=el("td");cbTd.appendChild(cb);tr.appendChild(cbTd);
    [a.strategy_key,a.sample_size,a.wins,a.losses,a.breakeven,
      a.win_rate===null?"—":a.win_rate+"%",a.profit_factor===null?"—":fmt(a.profit_factor),
      money(a.gross_pnl),money(a.cost),money(a.net_pnl),
      a.avg_r===null?"—":fmt(a.avg_r),a.total_r===null?"—":fmt(a.total_r),
      a.avg_holding_seconds===null?"—":Math.round(a.avg_holding_seconds/60)+" min",
      a.max_drawdown===null?"—":money(a.max_drawdown)
    ].forEach(v=>tr.appendChild(el("td",String(v))));
    body.appendChild(tr);
  });
  t1.appendChild(body);table1.appendChild(t1);wrap.appendChild(table1);
  wrap.appendChild(el("p","Sample size (N) is shown for every row. Max drawdown is computed only from the "+
    "currently filtered, chronologically-ordered trade sequence for that strategy.","safeguard"));
  wrap.appendChild(el("div","Per-strategy cumulative realized net P&L","mini-title"));
  agg.slice(0,6).forEach(a=>{
    wrap.appendChild(el("p",a.strategy_key,"tiny"));
    const chart=el("div",null,"bars");let running=0;const maxAbs=Math.max(0.01,...a.trades.map(t=>{running+=(t.net||0);return Math.abs(running);}));
    running=0;
    a.trades.forEach((t,i)=>{
      running+=(t.net||0);
      const col=el("div",null,"bar-col");col.title="Trade "+(i+1)+": cumulative "+money(round2(running));
      const bar=el("div",null,"bar"+(running<0?" negative":""));
      bar.style.height=String(Math.max(2,Math.round(Math.abs(running)/maxAbs*75)))+"px";
      col.appendChild(bar);chart.appendChild(col);
    });
    wrap.appendChild(chart);
  });
  wrap.appendChild(el("div","Gross P&L vs execution costs (filtered set, per strategy)","mini-title"));
  wrap.appendChild(table(agg.map(a=>({strategy_key:a.strategy_key,gross_pnl:money(a.gross_pnl),
    costs:money(a.cost),net_pnl:money(a.net_pnl)}))));
  const bySymbol={},byRegime={};
  filtered.forEach(r=>{
    bySymbol[r.symbol]=(bySymbol[r.symbol]||0)+(r.net||0);
    const rg=r.regime||"UNKNOWN";byRegime[rg]=(byRegime[rg]||0)+(r.net||0);
  });
  wrap.appendChild(el("div","Results by symbol (filtered set)","mini-title"));
  wrap.appendChild(table(Object.entries(bySymbol).map(([symbol,net])=>({symbol,net_pnl:money(round2(net))}))));
  wrap.appendChild(el("div","Results by regime (filtered set)","mini-title"));
  wrap.appendChild(table(Object.entries(byRegime).map(([regime,net])=>({regime,net_pnl:money(round2(net))}))));
  const byDay={};filtered.forEach(r=>{if(!r.exit_time)return;const day=new Date(r.exit_time*1000).toISOString().slice(0,10);
    byDay[day]=(byDay[day]||0)+1;});
  wrap.appendChild(el("div","Trade frequency over time (filtered set)","mini-title"));
  wrap.appendChild(table(Object.entries(byDay).sort().map(([day,n])=>({day,trade_count:n}))));
  if(S.shortlist.length){
    wrap.appendChild(el("div","Human strategy review — shortlist (up to 3, review-only, no auto-activation)","mini-title"));
    const shortlisted=S.shortlist.map(k=>{
      const [tab,key]=k.split(":");
      const a2=tab===S.tab?agg.find(x=>x.strategy_key===key):null;
      return a2?{...a2,tab}:{strategy_key:key,tab,sample_size:"—",note:"switch to the "+tab.toUpperCase()+" tab to see this strategy's current figures"};
    });
    wrap.appendChild(table(shortlisted.map(a=>({tab:a.tab,strategy_key:a.strategy_key,sample_size:a.sample_size,
      win_rate:a.win_rate!==undefined?a.win_rate:"—",net_pnl:a.net_pnl!==undefined?money(a.net_pnl):"—",
      profit_factor:a.profit_factor!==undefined?fmt(a.profit_factor):"—"}))));
    const exportBtn=el("button","Export shortlist (JSON)");exportBtn.type="button";exportBtn.className="secondary-button";
    exportBtn.addEventListener("click",()=>downloadText("strategy-shortlist-"+S.tab+".json",JSON.stringify(shortlisted,null,2)));
    const exportCsv=el("button","Export shortlist (CSV)");exportCsv.type="button";exportCsv.className="secondary-button";
    exportCsv.addEventListener("click",()=>downloadText("strategy-shortlist-"+S.tab+".csv",
      csv(shortlisted.map(a=>({tab:a.tab,strategy_key:a.strategy_key,sample_size:a.sample_size,
        win_rate:a.win_rate,net_pnl:a.net_pnl,profit_factor:a.profit_factor}))),"text/csv"));
    const btnRow=el("div",null,"toolbar");btnRow.appendChild(exportBtn);btnRow.appendChild(exportCsv);
    wrap.appendChild(btnRow);
  }else{
    wrap.appendChild(el("p","Check up to 3 \"Compare\" boxes above to build a review-only shortlist you can export.","empty"));
  }
  wrap.appendChild(el("div","Backtest results are historical evaluations, never current DEMO results. This "+
    "view cannot activate, promote or reconfigure a strategy — any change requires a reviewed code/config "+
    "change, tests and an operator-approved deployment.","safeguard"));
  return wrap;
}
function multiPositionPanel(p){
  const wrap=el("div");
  wrap.appendChild(el("p",p.headline,"safeguard"));
  const rc=p.risk_capacity||{};const cur=p.account_currency||"";
  wrap.appendChild(table([{
    max_positions:p.max_open_positions,open_positions:p.open_positions,pending_orders:p.pending_orders,
    remaining_slots:p.remaining_position_slots,open_risk:money(p.open_monetary_risk,cur),
    pending_risk:money(p.pending_monetary_risk,cur),
    remaining_aggregate_risk:rc.status?rc.status+": "+rc.detail:money(rc.remaining_aggregate_risk,cur),
    enabled_symbols:(p.enabled_symbols||[]).join(", ")||"None"}]));
  if((p.positions||[]).length){
    wrap.appendChild(el("div","Open positions and the strategy that opened each","mini-title"));
    wrap.appendChild(table(p.positions.map(x=>({symbol:x.symbol,direction:x.direction,volume:x.volume,
      strategy:x.strategy_key,position:x.broker_position_id,initial_risk:money(x.initial_monetary_risk,cur),
      floating_pnl:x.floating_pnl===null?"N/A (no fresh broker sample)":money(x.floating_pnl,cur),
      opened_utc:x.opened_at_utc}))));
    const links=el("div",null,"lab-links");
    p.positions.forEach(x=>{
      const b=el("button","Strategy Lab history: "+x.symbol+" #"+x.broker_position_id+" ("+x.strategy_key+")");
      b.type="button";b.className="secondary-button";
      b.addEventListener("click",()=>{current="strategy_lab";LAB.evidence="DEMO";LAB.filters={};showView();
        labRender();labLoad(true);labOpenTrade("DEMO",String(x.broker_position_id));});
      links.appendChild(b);
    });
    wrap.appendChild(links);
  }
  wrap.appendChild(el("div","Why no second trade — per symbol","mini-title"));
  wrap.appendChild(table(Object.entries(p.symbols||{}).map(([symbol,v])=>({symbol,status:v.status,detail:v.detail,
    bar:v.bar_status,open_position_strategy:v.open_position_strategy,quote:v.quote_status,
    quote_age_s:v.quote_age_seconds,signal:v.signal_status,
    latest_signals:(v.latest_signals&&v.latest_signals.signals.length)?v.latest_signals.signals.map(s=>
      s.strategy_key+" "+(s.direction||"")+(s.raw_confidence!=null?" raw score="+Number(s.raw_confidence).toFixed(2):"")).join("; "):"none",
    cost_eligible:v.cost_eligible,news:v.news,
    last_decision:v.last_decision?(v.last_decision.stage+" "+v.last_decision.decision+
      (v.last_decision.strategy_key?" ("+v.last_decision.strategy_key+")":"")):null,
    last_decision_utc:v.last_decision?v.last_decision.decided_at_utc:null,
    last_actual_block:v.last_block?(v.last_block.label+" — "+(v.last_block.reason||"")):"none recorded",
    last_block_utc:v.last_block?v.last_block.decided_at_utc:null}))));
  wrap.appendChild(el("div","Pairwise correlation (aligned M5 returns)","mini-title"));
  const corr=(p.correlation||[]).map(c=>({pair:c.pair.join(" / "),
    correlation:c.correlation===null?"N/A":Number(c.correlation).toFixed(3),sample_size:c.sample_size,
    threshold:c.threshold,decision_if_other_open:c.decision,live_now:c.evaluated_now,reason:c.reason}));
  wrap.appendChild(corr.length?table(corr):el("p",p.readiness_age_seconds===null?
    "The running runtime does not publish correlation diagnostics yet (older build).":
    "Fewer than two enabled symbols: no pair to correlate.","empty"));
  wrap.appendChild(el("p",p.guarantee,"safeguard"));
  return wrap;
}
function panelBody(name,raw){
  const p=raw||{};
  if(p.status==="UNAVAILABLE"||p.status==="NO_DATA")
    return el("p",fmt(p.status)+": "+fmt(p.detail),p.status==="UNAVAILABLE"?"error":"empty");
  if(name==="market")return marketPanel(p);
  if(name==="performance")return performancePanel(p);
  if(name==="strategy_registry")return stratRegistryPanel(p);
  if(name==="strategy_activity")return stratActivityPanel(p);
  if(name==="strategy_attribution")return stratAttributionPanel(p);
  if(name==="strategy_performance")return stratPerformancePanel(p);
  if(name==="multi_position")return multiPositionPanel(p);
  const copy=Object.assign({},p);delete copy.status;return render(copy,0);
}
let current="overview",data=null,feedState="CONNECTING",pollHandle=null,polling=false,ws=null,retry=null;
const nav=document.getElementById("nav"),cards={},lastContent={};
GROUPS.forEach(g=>{
  const b=el("button",g.label);b.type="button";b.dataset.view=g.id;
  b.addEventListener("click",()=>{current=g.id;document.getElementById("search").value="";showView();
    if(g.id==="strategy_lab"){labRender();labLoad(true);}});
  nav.appendChild(b);
});
const root=document.getElementById("panels");
FRIENDLY && Object.keys(FRIENDLY).forEach(name=>{
  const section=el("section",null,"panel"+(WIDE.has(name)?" wide":""));section.id="panel-"+name;
  const head=el("div",null,"panel-head");head.appendChild(el("h3",FRIENDLY[name]));
  head.appendChild(el("span","Awaiting runtime data","source"));section.appendChild(head);
  const body=el("div",null,"panel-body");body.tabIndex=0;section.appendChild(body);root.appendChild(section);
  cards[name]={section,body,source:head.lastChild};
});
const kpis=[
  ["Account equity","Actual DEMO account snapshot"],["Floating P&L","Observed DEMO broker positions"],
  ["DEMO booked net","Today's recorded deals"],["Broker positions","Fresh DEMO terminal snapshot"],
  ["Dangerous UNKNOWN","Active unresolved orders"],["Feed freshness","Runtime and broker data"],
  ["Account balance","Actual DEMO account snapshot"],["MT5 & account","Terminal connection and trade mode"],
  ["Total risk","Local open + pending, % of fresh equity (ceiling 0.75 %)"]
];
kpis.forEach(([label,hint],i)=>{
  const k=el("div",null,"kpi");k.appendChild(el("span",label,"label"));
  const v=el("div","—","value");v.id="kpi-"+i;k.appendChild(v);
  k.appendChild(el("div",hint,"hint"));document.getElementById("kpis").appendChild(k);
});
function pill(id,message,kind){
  const e=document.getElementById(id);e.textContent=message;e.className="pill "+kind;
}
function paintSummary(){
  if(!data)return;
  const p=data.panels||{},ov=p.overview||{},mk=p.market||{},ord=p.orders||{},risk=p.risk||{};
  const rt=ov.runtime||{},ks=ov.kill_switch||{},acct=mk.account||{};
  // The dashboard's OWN data can be old too (server stopped, network): then
  // nothing below may be presented as current.
  const dataAge=Math.max(0,Math.round(Date.now()/1000-Number(data.generated_at_utc||0)));
  const snapshotStale=feedState==="OFFLINE"||dataAge>15;
  const live=!snapshotStale&&mk.status==="OK"&&mk.age_seconds<=15&&acct.trade_mode==="DEMO";
  const runtimeFresh=!snapshotStale&&(rt.status==="RUNNING"||rt.status==="DEGRADED");
  const rtStatus=snapshotStale?"UNKNOWN (dashboard data "+dataAge+"s old)":
    runtimeFresh?"RUNNING"+(rt.mode?" · "+rt.mode:""):fmt(rt.status);
  pill("runtime","Runtime: "+rtStatus,runtimeFresh?"good":"bad");
  pill("health","Health: "+(snapshotStale?"UNKNOWN":fmt(ov.health)),
    ov.health==="HEALTHY"&&runtimeFresh?"good":ov.health==="CRITICAL"?"bad":"warn");
  pill("kill","Kill switch: "+fmt(ks.status)+(snapshotStale?" (last known)":""),
    ks.blocks_new_entries||snapshotStale?"warn":"good");
  pill("feed",feedState==="WS"?"Dashboard: live":feedState==="POLL"?"Dashboard: polling":"Dashboard: offline",
    feedState==="WS"?"good":feedState==="POLL"?"warn":"bad");
  const curr=acct.currency||"";
  document.getElementById("kpi-0").textContent=live?money(acct.equity,curr):"—";
  const brokerPositions=mk.broker_positions;
  const floatValid=live&&Array.isArray(brokerPositions)&&brokerPositions.every(x=>typeof x.floating_pnl==="number");
  document.getElementById("kpi-1").textContent=floatValid?
    money(brokerPositions.reduce((sum,x)=>sum+x.floating_pnl,0),curr):"—";
  document.getElementById("kpi-2").textContent=!snapshotStale&&typeof risk.demo_realized_pnl_today_utc==="number"?
    money(risk.demo_realized_pnl_today_utc,curr):"—";
  document.getElementById("kpi-3").textContent=live&&Array.isArray(brokerPositions)?
    fmt(brokerPositions.length):"—";
  document.getElementById("kpi-4").textContent=!snapshotStale&&Array.isArray(ord.dangerous_unknown)?
    fmt(ord.dangerous_unknown.length):"—";
  document.getElementById("kpi-5").textContent=runtimeFresh&&live?"Fresh":runtimeFresh?"Broker stale":"Unavailable";
  document.getElementById("kpi-6").textContent=live&&typeof acct.balance==="number"?money(acct.balance,curr):"—";
  const term=mk.terminal||{};
  document.getElementById("kpi-7").textContent=snapshotStale?"Unknown":mk.status==="NO_DATA"||!mk.status?"Unavailable":
    mk.status!=="OK"?"Stale":(term.connected?"Connected":"Disconnected")+" · "+fmt(acct.trade_mode);
  const totalRisk=risk.estimated_total_risk_pct_of_fresh_equity;
  document.getElementById("kpi-8").textContent=!snapshotStale&&typeof totalRisk==="number"?totalRisk.toFixed(2)+" %":"—";
  const alarm=document.getElementById("alert"),issues=[];
  if(snapshotStale)issues.push("Dashboard server unreachable or not updating: the last data is "+dataAge+
    "s old, so runtime and broker figures are not shown as current.");
  if(!runtimeFresh)issues.push("Runtime is not reporting a fresh running heartbeat.");
  if(!live)issues.push("Fresh verified MT5 DEMO telemetry is unavailable; displayed figures may be old.");
  if(Array.isArray(ord.dangerous_unknown)&&ord.dangerous_unknown.length)
    issues.push(String(ord.dangerous_unknown.length)+" dangerous UNKNOWN/PENDING_RECONCILIATION orders require investigation.");
  const truth=ord.broker_truth||{};
  if(rt.mode==="DEMO"&&truth.status&&truth.status!=="AVAILABLE")
    issues.push("BROKER TRUTH "+String(truth.status)+" since "+fmt(truth.since)+": "+String(truth.error||"")+
      ". Reconciliation shows the last proven verdict only; new exposure is blocked until a full position cycle succeeds.");
  if(Array.isArray(ord.unresolved_closes)&&ord.unresolved_closes.length)
    issues.push(String(ord.unresolved_closes.length)+" close request(s) with an UNPROVEN outcome (position "+
      ord.unresolved_closes.map(c=>String(c.broker_position_id)).join(", ")+"): never resent; new exposure blocked "+
      "until broker truth resolves them.");
  if(acct.trade_mode&&acct.trade_mode!=="DEMO")issues.push("NON-DEMO ACCOUNT DETECTED: broker mutations must remain blocked.");
  if(rt.mode==="DEMO"&&mk.status==="OK"&&mk.terminal&&
    (!mk.terminal.trade_allowed||acct.trade_allowed===false||acct.trade_expert===false))
    issues.push("MT5 or account Algo Trading is disabled; new DEMO execution should remain blocked.");
  const clockStatus=mk.server_clock||{};
  if(clockStatus.verdict&&clockStatus.verdict!=="VERIFIED")
    issues.push("Broker timestamp validation: "+String(clockStatus.verdict)+".");
  const cfg=risk.configured_limits||{};
  if(["risk_per_trade_pct","max_total_open_risk_pct","max_daily_loss_pct","max_drawdown_pct"].some(
    (key,i)=>typeof cfg[key]==="number"&&cfg[key]>[0.25,0.75,2,5][i]+1e-9)||
    (typeof cfg.max_open_positions==="number"&&cfg.max_open_positions>2)||
    (typeof cfg.max_positions_per_symbol==="number"&&cfg.max_positions_per_symbol>1))
    issues.push("Configured risk limits exceed documented ceilings; review config before permitting new exposure.");
  const calendar=((p.news||{}).runtime_snapshot||{});
  if(calendar.health&&calendar.health!=="HEALTHY"&&calendar.health!=="DEGRADED")
    issues.push("Economic calendar: "+String(calendar.health)+". New exposure must respect the fail-closed news gate.");
  const inc=((p.events||{}).open_execution_incidents||[]);
  if(Array.isArray(inc)&&inc.length)
    issues.push(String(inc.length)+" unresolved execution incident(s) are recorded.");
  if(issues.length){alarm.textContent=issues.join(" ");alarm.className="warning-banner visible";}
  else{alarm.textContent="";alarm.className="warning-banner";}
  document.getElementById("data-age").textContent=
    "Snapshot "+(data.generated_at_utc?time(data.generated_at_utc):"unavailable")+
    " · Broker telemetry "+(mk.age_seconds===undefined?"unavailable":fmt(mk.age_seconds)+"s old");
  document.getElementById("clock").textContent="Your local time: "+new Date().toLocaleString();
}
/* ================= Strategy Lab (on-demand, read-only) ================= */
const LAB={evidence:"DEMO",sub:"comparison",filters:{},run_id:"",session_key:"",page:1,q:"",research:null,
  summary:null,loading:false,error:null,detailKey:"",detail:null,trade:null,trades:null,
  compare:(()=>{try{return JSON.parse(localStorage.getItem("asn-lab-compare")||"[]");}catch(e){return [];}})(),
  shortlist:(()=>{try{return JSON.parse(localStorage.getItem("asn-lab-shortlist")||"[]");}catch(e){return [];}})(),
  lastFetch:0,seq:0};
const LAB_SUBS=[["comparison","Comparison"],["detail","Strategy detail"],["compare","Compare (max 3)"],
  ["trades","All trades"],["unattributed","Unattributed"],["reconciliation","Reconciliation"],["shortlist","Review shortlist"],
  ["readiness","Multi-position readiness"]];
const LAB_FILTERS=[["strategy","Strategy"],["symbol","Symbol"],["version","Version"],["regime","Regime"],
  ["direction","Direction"],["session","Session"],["exit_reason","Exit reason"],["cost_provenance","Cost provenance"],
  ["source_class","Source"]];
const labRoot=()=>document.getElementById("lab");
function labSave(key,val){try{localStorage.setItem(key,JSON.stringify(val));}catch(e){}}
function labColor(key){
  const keys=(LAB.summary&&LAB.summary.active_strategy_keys)||["momentum_continuation","pullback_continuation",
    "range_breakout","statistical_reversion","volatility_expansion","microstructure_acceleration"];
  const i=keys.indexOf(key);return i>=0&&i<6?"var(--s"+(i+1)+")":"var(--muted)";
}
function labQuery(extra){
  const p=new URLSearchParams();p.set("evidence",LAB.evidence);
  Object.entries(LAB.filters).forEach(([k,v])=>{if(v)p.set(k,v);});
  if(LAB.evidence==="BACKTEST"&&LAB.run_id)p.set("run_id",LAB.run_id);
  if(LAB.evidence==="PAPER"&&LAB.session_key)p.set("session_key",LAB.session_key);
  Object.entries(extra||{}).forEach(([k,v])=>{if(v!==undefined&&v!==null&&v!=="")p.set(k,v);});
  return p.toString();
}
async function labGet(url){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
  try{const r=await fetch(url,{cache:"no-store",signal:controller.signal});
    if(!r.ok)throw Error("HTTP "+r.status+" "+(await r.text()).slice(0,200));return await r.json();}
  finally{clearTimeout(timer);}
}
async function labLoad(force){
  if(current!=="strategy_lab")return;
  const seq=++LAB.seq;LAB.loading=true;if(force)labRender();
  if(LAB.evidence==="RESEARCH"){
    try{const r=await labGet("/api/strategy-lab/research");if(seq===LAB.seq){LAB.research=r;LAB.error=null;LAB.lastFetch=Date.now();}}
    catch(err){if(seq===LAB.seq)LAB.error=String(err);}
    finally{if(seq===LAB.seq){LAB.loading=false;labRender();}}
    return;
  }
  try{
    const s=await labGet("/api/strategy-lab/summary?"+labQuery());
    if(seq!==LAB.seq)return;
    if(s.status==="UNAVAILABLE")throw Error(s.detail);
    LAB.summary=s;LAB.error=null;LAB.lastFetch=Date.now();
    if(LAB.evidence==="BACKTEST"&&!LAB.run_id&&s.run_id)LAB.run_id=s.run_id;
    if(LAB.sub==="trades"||LAB.sub==="unattributed")await labLoadTrades(seq);
    if(LAB.sub==="detail"&&LAB.detailKey)await labLoadDetail(seq);
  }catch(err){if(seq===LAB.seq)LAB.error=String(err);}
  finally{if(seq===LAB.seq){LAB.loading=false;labRender();}}
}
async function labLoadTrades(seq){
  const extra={page:LAB.page,page_size:50,q:LAB.q};
  if(LAB.sub==="unattributed")extra.strategy="UNATTRIBUTED";
  const t=await labGet("/api/strategy-lab/trades?"+labQuery(extra));
  if(seq===LAB.seq)LAB.trades=t;
}
async function labLoadDetail(seq){
  const d=await labGet("/api/strategy-lab/strategy/"+encodeURIComponent(LAB.detailKey));
  if(seq===LAB.seq)LAB.detail=d;
}
async function labOpenTrade(evidence,id){
  LAB.trade={loading:true,id};labRender();
  try{LAB.trade=await labGet("/api/strategy-lab/trade/"+evidence+"/"+encodeURIComponent(id));}
  catch(err){LAB.trade={error:String(err),id};}
  labRender();
  const d=document.getElementById("lab-lifecycle");if(d)d.scrollIntoView({block:"start"});
}
/* ---------- formatting ---------- */
function mcur(){return (LAB.summary&&LAB.summary.currency)||"(currency not recorded)";}
function signed(n,cur){
  const span=el("span");
  if(typeof n!=="number"||!Number.isFinite(n)){span.textContent="N/A";span.className="na";return span;}
  const v=Math.round(n*100)/100;
  span.textContent=(v>0?"+":v<0?"−":"")+money(Math.abs(v))+" "+(cur||mcur());
  span.className=v>0?"pos":v<0?"neg":"";return span;
}
const pct=(x)=>typeof x==="number"?(x*100).toFixed(1)+" %":"N/A";
const num=(x,d)=>typeof x==="number"&&Number.isFinite(x)?x.toFixed(d===undefined?2:d):"N/A";
const dur=(s)=>typeof s!=="number"?"N/A":s<120?Math.round(s)+" s":s<7200?Math.round(s/60)+" min":(s/3600).toFixed(1)+" h";
function cell(tr,content,cls){const td=el("td");if(content instanceof Node)td.appendChild(content);else td.textContent=content;
  if(cls)td.className=cls;tr.appendChild(td);return td;}
function gridTable(headers,rows,opts){
  const scroll=el("div",null,"table-scroll");const t=el("table");const hd=el("thead");const hr=el("tr");
  headers.forEach(h=>{const th=el("th",Array.isArray(h)?h[0]:h);if(Array.isArray(h)&&h[1])th.className=h[1];
    th.scope="col";hr.appendChild(th);});
  hd.appendChild(hr);t.appendChild(hd);const b=el("tbody");rows.forEach(r=>b.appendChild(r));t.appendChild(b);
  if(opts&&opts.nowrap)t.classList.add("nowrap-table");
  if(opts&&opts.caption){const c=el("caption",opts.caption);c.className="tiny";c.style.textAlign="left";c.style.padding="6px";t.prepend(c);}
  scroll.appendChild(t);return scroll;
}
/* ---------- charts (inline SVG, hover tooltip, legend + direct labels + table view) ---------- */
const SVGNS=document.getElementById("svg-ns").namespaceURI; /* read from an inline <svg>: no URL literal */
function sv(tag,attrs){const n=document.createElementNS(SVGNS,tag);Object.entries(attrs||{}).forEach(([k,v])=>n.setAttribute(k,v));return n;}
const tipEl=()=>document.getElementById("tip");
function showTip(evt,text){const t=tipEl();t.textContent=text;t.style.display="block";
  const x=Math.min(window.innerWidth-260,evt.clientX+14),y=Math.min(window.innerHeight-60,evt.clientY+14);
  t.style.left=x+"px";t.style.top=y+"px";}
function hideTip(){tipEl().style.display="none";}
function lineChart(series,opts){
  /* series: [{key,label,color,points:[{x,y,tip}]}] -- one shared y scale, never a second axis */
  const W=640,H=230,L=58,R=118,T=12,B=26;const wrap=el("div",null,"chart");
  const pts=series.flatMap(s=>s.points);
  if(!pts.length){wrap.appendChild(el("p","No closed trades in the current selection.","empty"));return wrap;}
  let x0=Math.min(...pts.map(p=>p.x)),x1=Math.max(...pts.map(p=>p.x));if(x1===x0){x0-=3600;x1+=3600;}
  let y0=Math.min(0,...pts.map(p=>p.y)),y1=Math.max(0,...pts.map(p=>p.y));if(y1===y0){y1+=1;}
  const X=v=>L+(v-x0)/(x1-x0)*(W-L-R),Y=v=>T+(y1-v)/(y1-y0)*(H-T-B);
  const svg=sv("svg",{viewBox:"0 0 "+W+" "+H,role:"img","aria-label":opts.aria||"line chart"});
  [y0,(y0+y1)/2,y1].forEach(v=>{svg.appendChild(sv("line",{x1:L,x2:W-R,y1:Y(v),y2:Y(v),class:"axis","stroke-dasharray":"2 4"}));
    const t=sv("text",{x:L-6,y:Y(v)+3,"text-anchor":"end"});t.textContent=Math.round(v*100)/100;svg.appendChild(t);});
  svg.appendChild(sv("line",{x1:L,x2:W-R,y1:Y(0),y2:Y(0),class:"axis"}));
  [x0,x1].forEach((v,i)=>{const t=sv("text",{x:X(v),y:H-8,"text-anchor":i?"end":"start"});
    t.textContent=new Date(v*1000).toISOString().slice(0,10);svg.appendChild(t);});
  series.forEach(s=>{
    if(!s.points.length)return;
    const d=s.points.map((p,i)=>(i?"L":"M")+X(p.x).toFixed(1)+" "+Y(p.y).toFixed(1)).join(" ");
    svg.appendChild(sv("path",{d,fill:"none",stroke:s.color,"stroke-width":2,"stroke-linejoin":"round"}));
    s.points.forEach(p=>{
      svg.appendChild(sv("circle",{cx:X(p.x),cy:Y(p.y),r:s.points.length>60?2:3.5,fill:s.color,stroke:"var(--surface)","stroke-width":1.5}));
      const hit=sv("circle",{cx:X(p.x),cy:Y(p.y),r:9,fill:"transparent"});
      hit.addEventListener("mousemove",e=>showTip(e,s.label+" · "+p.tip));hit.addEventListener("mouseleave",hideTip);
      svg.appendChild(hit);
    });
    const last=s.points[s.points.length-1];
    const lab=sv("text",{x:X(last.x)+6,y:Y(last.y)+3});lab.textContent=s.label.slice(0,18);
    lab.setAttribute("style","fill:var(--text)");svg.appendChild(lab);
  });
  wrap.appendChild(svg);
  if(series.length>1){const lg=el("div",null,"legend");series.forEach(s=>{const it=el("span");const sw=el("span",null,"swatch");
    sw.style.background=s.color;it.appendChild(sw);it.appendChild(document.createTextNode(s.label+" ("+s.points.length+" closed)"));lg.appendChild(it);});
    wrap.appendChild(lg);}
  return wrap;
}
function barChart(items,opts){
  /* items: [{label,value,tip,color}] diverging around zero: positive = --pos, negative = --neg */
  const wrap=el("div",null,"chart");
  if(!items.length){wrap.appendChild(el("p","No data in the current selection.","empty"));return wrap;}
  const W=640,rowH=22,L=190,R=96,H=items.length*rowH+10;
  const maxAbs=Math.max(1e-9,...items.map(i=>Math.abs(i.value||0)));
  const hasNeg=items.some(i=>(i.value||0)<0),zero=hasNeg?L+(W-L-R)/2:L;
  const scale=(W-L-R)/(hasNeg?2:1)/maxAbs;
  const svg=sv("svg",{viewBox:"0 0 "+W+" "+H,role:"img","aria-label":opts.aria||"bar chart"});
  svg.appendChild(sv("line",{x1:zero,x2:zero,y1:0,y2:H,class:"axis"}));
  items.forEach((it,i)=>{
    const v=it.value||0,y=5+i*rowH,w=Math.max(1,Math.abs(v)*scale);
    const t=sv("text",{x:L-8,y:y+14,"text-anchor":"end"});t.textContent=String(it.label).slice(0,30);svg.appendChild(t);
    svg.appendChild(sv("rect",{x:v<0?zero-w:zero,y:y+3,width:w,height:rowH-8,rx:3,fill:it.color||(v<0?"var(--neg)":"var(--pos)")}));
    const vt=sv("text",{x:v<0?zero+4:zero+w+4,y:y+14,"text-anchor":"start"});
    vt.setAttribute("style","fill:var(--text)");vt.textContent=it.valueLabel||(Math.round(v*100)/100);svg.appendChild(vt);
    const hit=sv("rect",{x:0,y,width:W,height:rowH,fill:"transparent"});
    hit.addEventListener("mousemove",e=>showTip(e,it.tip||(it.label+": "+(Math.round(v*100)/100))));
    hit.addEventListener("mouseleave",hideTip);svg.appendChild(hit);
  });
  wrap.appendChild(svg);return wrap;
}
function chartCard(title,chart,tableNode,note){
  const c=el("div",null,"chart-card");c.appendChild(el("h4",title));c.appendChild(chart);
  if(note)c.appendChild(el("p",note,"lab-note"));
  if(tableNode){const d=el("details");d.appendChild(el("summary","Table view"));d.appendChild(tableNode);c.appendChild(d);}
  return c;
}
/* ---------- controls ---------- */
function labControls(){
  const box=el("div",null,"lab");
  const top=el("div",null,"lab-bar");
  const seg=el("div",null,"seg");seg.setAttribute("role","tablist");seg.setAttribute("aria-label","Evidence source");
  ["DEMO","PAPER","BACKTEST","RESEARCH"].forEach(ev=>{const b=el("button",ev==="RESEARCH"?"INDEPENDENT RESEARCH":ev);
    b.type="button";b.setAttribute("role","tab");
    b.setAttribute("aria-selected",String(LAB.evidence===ev));
    b.addEventListener("click",()=>{if(LAB.evidence===ev)return;LAB.evidence=ev;LAB.filters={};LAB.page=1;LAB.trade=null;
      LAB.trades=null;LAB.summary=null;labLoad(true);});seg.appendChild(b);});
  top.appendChild(seg);
  const s=LAB.summary;
  if(LAB.evidence==="BACKTEST"&&s&&s.runs){
    const lab=el("label","Research run");const sel=el("select");sel.setAttribute("aria-label","Backtest run");
    s.runs.forEach(r=>{const o=el("option",r.run_id+" · "+r.canonical_symbol+" · "+fmt(r.trade_count)+" trades · "+fmt(r.cost_provenance));
      o.value=r.run_id;sel.appendChild(o);});
    sel.value=LAB.run_id||s.run_id||"";sel.addEventListener("change",()=>{LAB.run_id=sel.value;LAB.page=1;labLoad(true);});
    lab.appendChild(sel);top.appendChild(lab);
  }
  if(LAB.evidence==="PAPER"&&s&&s.sessions&&s.sessions.length){
    const lab=el("label","PAPER session");const sel=el("select");const all=el("option","All sessions (per-trade statistics only)");
    all.value="";sel.appendChild(all);
    s.sessions.forEach(x=>{const o=el("option",x.session_key+" · "+x.symbol+" · "+x.trades);o.value=x.session_key;sel.appendChild(o);});
    sel.value=LAB.session_key;sel.addEventListener("change",()=>{LAB.session_key=sel.value;labLoad(true);});
    lab.appendChild(sel);top.appendChild(lab);
  }
  const status=el("span",null,"lab-note");status.setAttribute("role","status");
  if(LAB.loading)status.textContent="Loading…";
  else if(LAB.error)status.textContent="Error: "+LAB.error;
  else if(s){const fr=s.freshness||{};status.textContent="Computed "+time(fr.computed_at_utc||s.generated_at_utc)+
    (fr.cache?" · "+(fr.cache==="HIT"?"cached (database unchanged)":"freshly computed"):"")+" · "+fmt(s.filtered_trade_records)+
    " of "+fmt(s.total_trade_records)+" trade records match · currency "+mcur();}
  top.appendChild(status);
  const rb=el("button","Reload");rb.type="button";rb.className="secondary-button";rb.addEventListener("click",()=>labLoad(true));
  top.appendChild(rb);box.appendChild(top);
  if(LAB.evidence==="RESEARCH")return box;
  const fbar=el("div",null,"lab-bar");fbar.setAttribute("aria-label","Strategy Lab filters");
  const dateInput=(key,label)=>{const l=el("label",label);const i=el("input");i.type="date";i.value=LAB.filters[key]||"";
    i.addEventListener("change",()=>{LAB.filters[key]=i.value;LAB.page=1;labLoad(true);});l.appendChild(i);fbar.appendChild(l);};
  dateInput("date_from","From (UTC)");dateInput("date_to","To (UTC, inclusive)");
  const opts=(s&&s.filter_options)||{};
  LAB_FILTERS.forEach(([key,label])=>{
    const l=el("label",label);const sel=el("select");const a=el("option","All");a.value="";sel.appendChild(a);
    let values=(opts[key]||[]).slice();
    if(key==="strategy"){values=Array.from(new Set([...(s?s.active_strategy_keys:[]),...values]));values.push("UNATTRIBUTED");}
    values.forEach(v=>{const o=el("option",v);o.value=v;sel.appendChild(o);});
    sel.value=LAB.filters[key]||"";sel.addEventListener("change",()=>{LAB.filters[key]=sel.value;LAB.page=1;labLoad(true);});
    l.appendChild(sel);fbar.appendChild(l);
  });
  const reset=el("button","Clear filters");reset.type="button";reset.className="secondary-button";
  reset.addEventListener("click",()=>{LAB.filters={};LAB.page=1;labLoad(true);});fbar.appendChild(reset);
  box.appendChild(fbar);
  const sub=el("div",null,"seg");sub.setAttribute("role","tablist");sub.setAttribute("aria-label","Strategy Lab sections");
  LAB_SUBS.forEach(([id,label])=>{const b=el("button",label);b.type="button";b.setAttribute("role","tab");
    b.setAttribute("aria-selected",String(LAB.sub===id));
    b.addEventListener("click",()=>{LAB.sub=id;LAB.page=1;LAB.trades=null;LAB.trade=null;
      if(id==="detail"&&!LAB.detailKey&&s)LAB.detailKey=s.active_strategy_keys[0];labLoad(true);});sub.appendChild(b);});
  box.appendChild(sub);
  if(s&&s.evidence_note)box.appendChild(el("div",s.evidence_note,"safeguard"));
  return box;
}
/* ---------- sections ---------- */
function labCosts(r){
  /* recorded transaction costs (commission + fees + swap) with their recorded sign: a cost is negative;
     N/A when any component is unrecorded */
  if([r.commission,r.fee,r.swap].some(v=>typeof v!=="number"))return null;
  return (r.commission||0)+(r.fee||0)+(r.swap||0);
}
function labGrossNet(s){
  /* gross versus recorded costs versus net, totals of this evidence source only */
  const rows=s.strategies.filter(r=>r.closed_trades);
  const tot=(k)=>rows.reduce((a,r)=>a+(typeof r[k]==="number"?r[k]:0),0);
  const costs=rows.reduce((a,r)=>a+(labCosts(r)||0),0);
  const box=el("div",null,"lab-grossnet");
  box.appendChild(el("div","Gross versus net · attributed "+s.evidence+" trades","mini-title"));
  const tr=el("tr");cell(tr,fmt(rows.reduce((a,r)=>a+r.closed_trades,0)),"num");cell(tr,signed(tot("gross_pnl")),"num");
  cell(tr,signed(costs),"num");cell(tr,signed(tot("net_pnl")),"num");
  cell(tr,tot("gross_pnl")!==0?num(Math.abs(costs)/Math.abs(tot("gross_pnl")))+" ×":"N/A","num");
  box.appendChild(gridTable([["Closed trades","num"],["Gross P&L","num"],["Recorded costs","num"],["Net P&L","num"],
    ["|Costs| ÷ |gross|","num"]],[tr]));
  box.appendChild(el("p",s.evidence==="DEMO"?"DEMO gross is the broker's trade profit; recorded costs are broker commission, fees and swap. "+
    "Spread and slippage are already inside the broker price, so real friction is larger than the recorded costs shown.":
    "Simulated gross is mid-to-mid; costs include simulated spread, slippage, commission and swap.","lab-note"));
  return box;
}
function labResearch(){
  const box=el("div",null,"lab");const r=LAB.research;
  if(!r){box.appendChild(el("p","Loading independent research…","empty"));return box;}
  box.appendChild(el("div",r.note,"safeguard"));
  if(r.status!=="OK"){box.appendChild(el("p",fmt(r.status)+": "+fmt(r.detail),"empty"));return box;}
  Object.entries(r.symbols).forEach(([symbol,x])=>{
    box.appendChild(el("div",symbol+" · "+fmt(x.bars)+" M5 bars · "+x.folds+" folds · "+
      (x.range?time(x.range[0])+" → "+time(x.range[1]):"")+" · costs "+fmt(x.cost_provenance)+
      " · identical inputs "+(x.inputs_identical?"YES":"NO")+" · file "+fmt(x.file),"mini-title"));
    const heads=["Session",["Trades","num"],["W / L / BE","num"],["Win rate","num"],["Gross","num"],["Costs","num"],["Net","num"],
      ["Avg gross R","num"],["Avg cost R","num"],["Avg net R","num"],["PF","num"],["Avg hold","num"],
      ["Positive folds","num"],["Halted folds","num"],["PSR (vs 0)","num"],["DSR","num"]];
    const rows=Object.entries(x.sessions).map(([k,v])=>{
      const tr=el("tr");const n=el("span",k==="__selector__"?"SELECTOR (all six, live logic)":k);
      if(k!=="__selector__"){n.style.borderLeft="3px solid "+labColor(k);n.style.paddingLeft="6px";}cell(tr,n);
      if(v.error){const td=cell(tr,"FAILED: "+v.error,"error");td.colSpan=15;return tr;}
      if(!v.trades){cell(tr,"0","num");const td=cell(tr,"NO TRADES","na");td.colSpan=14;return tr;}
      cell(tr,fmt(v.trades),"num");cell(tr,v.wins+" / "+v.losses+" / "+v.breakevens,"num");cell(tr,pct(v.win_rate),"num");
      cell(tr,num(v.gross_pnl),"num");cell(tr,num(v.total_cost),"num");cell(tr,signed(v.net_pnl," (sim.)"),"num");
      cell(tr,num(v.avg_gross_r,3),"num");cell(tr,num(v.avg_cost_r,3),"num");cell(tr,num(v.avg_net_r,3),"num");
      cell(tr,num(v.profit_factor),"num");cell(tr,dur(v.avg_holding_seconds),"num");
      cell(tr,fmt(v.positive_folds)+" / "+fmt(v.folds),"num");cell(tr,fmt(v.halted_folds),"num");
      cell(tr,num(v.psr_vs_zero,3),"num");cell(tr,v.dsr===null||v.dsr===undefined?"N/A":num(v.dsr,3),"num");
      return tr;
    });
    box.appendChild(gridTable(heads,rows,{nowrap:true}));
    const sel=x.selector||{};const ps=sel.per_strategy||{};
    const srows=Object.entries(ps).map(([k,v])=>{const tr=el("tr");cell(tr,k);
      cell(tr,fmt(v.signals),"num");cell(tr,fmt(v.selected),"num");cell(tr,pct(v.selection_share),"num");
      cell(tr,fmt(v.lost_to_higher_edge),"num");cell(tr,fmt(v.filter_rejected),"num");
      cell(tr,num(v.stated_p_selected),"num");cell(tr,pct(v.capped_confidence_share),"num");
      cell(tr,num(v.expected_net_r_selected,3),"num");cell(tr,num(v.expected_cost_r_selected,3),"num");
      cell(tr,num(v.realized_net_r_selected_matched,3)+" (n="+fmt(v.selected_matched)+")","num");
      cell(tr,num(v.realized_cost_r_selected_matched,3),"num");
      cell(tr,num(v.realized_net_r_lost_matched,3)+" (n="+fmt(v.lost_matched)+")","num");return tr;});
    box.appendChild(el("div","Selector study · "+symbol+" · "+fmt(sel.candidates)+" candidates, "+fmt(sel.selected)+
      " selected, "+fmt(sel.contested_bars)+" contested bars","mini-title"));
    box.appendChild(gridTable(["Strategy",["Signals","num"],["Selected","num"],["Share of selections","num"],
      ["Lost to higher edge","num"],["Filtered","num"],["Stated p (selected)","num"],["Confidence capped at 1.0","num"],
      ["Expected net R (selected)","num"],["Expected cost R","num"],["Realized net R (selected)","num"],
      ["Realized cost R","num"],["Realized net R (lost)","num"]],srows,{nowrap:true}));
    const pbo=x.pbo||{};
    box.appendChild(el("p","Probability of backtest overfitting (pick-the-best-strategy, CSCV): "+
      (pbo.computable?num(pbo.pbo,2)+" over "+pbo.combinations+" combinations":"not computable — "+fmt(pbo.reason))+
      ". Realized R is from each strategy's own independent session at the same bar close (matched candidates only).","lab-note"));
  });
  box.appendChild(el("p","Research is review-only evidence. It never changes live configuration, never promotes a strategy, "+
    "and the reserved out-of-sample interval (2026-07-01..2026-09-18) is never read by it.","lab-note"));
  return box;
}
function labComparison(s){
  const box=el("div",null,"lab");
  const heads=["Compare","Shortlist","Strategy","Version","Status",["Signals","num"],["Rejected (filter · lost)","num"],
    ["Selected","num"],["Submitted","num"],
    ["Filled","num"],["Closed","num"],["W / L / BE","num"],["Win rate","num"],["Gross profit (winners)","num"],
    ["Gross loss (losers)","num"],["Gross P&L","num"],["Commission","num"],
    ["Fees","num"],["Swap","num"],["Recorded costs","num"],["Net P&L","num"],["Profit factor","num"],["Avg R (n)","num"],["Expectancy","num"],
    ["Avg hold","num"],["Max DD (closed)","num"],["Spread / slippage evidence","num"],"Last executed",["Open","num"],["Unrealized (live)","num"]];
  const rows=s.strategies.map(r=>{
    const tr=el("tr");const f=r.funnel||{};
    const cb=el("input");cb.type="checkbox";cb.checked=LAB.compare.includes(r.strategy_key);
    cb.setAttribute("aria-label","Compare "+r.strategy_key);
    cb.addEventListener("change",()=>{if(cb.checked){if(LAB.compare.length>=3){cb.checked=false;
      window.alert("Compare holds at most 3 strategies. Untick one first.");return;}
      LAB.compare.push(r.strategy_key);}else LAB.compare=LAB.compare.filter(k=>k!==r.strategy_key);labSave("asn-lab-compare",LAB.compare);});
    cell(tr,cb);
    const sl=el("input");sl.type="checkbox";sl.checked=LAB.shortlist.includes(r.strategy_key);
    sl.setAttribute("aria-label","Shortlist "+r.strategy_key+" for research review");
    sl.addEventListener("change",()=>{LAB.shortlist=sl.checked?Array.from(new Set([...LAB.shortlist,r.strategy_key])):
      LAB.shortlist.filter(k=>k!==r.strategy_key);labSave("asn-lab-shortlist",LAB.shortlist);});
    cell(tr,sl);
    const name=el("button",r.strategy_key,"linkish");name.type="button";name.style.borderLeft="3px solid "+labColor(r.strategy_key);
    name.style.paddingLeft="6px";name.addEventListener("click",()=>{LAB.sub="detail";LAB.detailKey=r.strategy_key;LAB.detail=null;labLoad(true);});
    cell(tr,name);cell(tr,fmt(r.strategy_version)+(r.versions_in_evidence.length?" (evidence: v"+r.versions_in_evidence.join(", v")+")":""));
    cell(tr,r.registration_status);
    const fn=(k)=>s.evidence==="DEMO"?fmt(f[k]):"N/A";
    cell(tr,fn("signals"),"num");
    cell(tr,s.evidence==="DEMO"?fmt(f.signals_rejected)+" · "+fmt(f.proposals_rejected):"N/A","num");
    cell(tr,fn("selected_proposals"),"num");cell(tr,fn("orders_submitted"),"num");
    cell(tr,fn("orders_filled"),"num");cell(tr,fmt(r.closed_trades),"num");
    if(!r.closed_trades){const td=cell(tr,"NO CLOSED TRADES","na");td.colSpan=17;}
    else{
      cell(tr,r.wins+" / "+r.losses+" / "+r.breakevens,"num");cell(tr,pct(r.win_rate),"num");
      cell(tr,signed(r.gross_profit_of_winners),"num");cell(tr,signed(r.gross_loss_of_losers),"num");
      cell(tr,signed(r.gross_pnl),"num");cell(tr,signed(r.commission),"num");cell(tr,signed(r.fee),"num");cell(tr,signed(r.swap),"num");
      cell(tr,signed(labCosts(r)),"num");cell(tr,signed(r.net_pnl),"num");
      cell(tr,r.profit_factor!==null?num(r.profit_factor):(r.profit_factor_note||"N/A"),"num");
      cell(tr,r.avg_r!==null?num(r.avg_r)+" ("+r.r_sample+")":"N/A","num");cell(tr,signed(r.expectancy_per_trade),"num");
      cell(tr,dur(r.avg_holding_seconds),"num");
      cell(tr,r.max_drawdown_closed_trade_basis===null?"N/A":signed(-r.max_drawdown_closed_trade_basis),"num");
      const ev=r.spread_slippage_evidence||{};
      cell(tr,s.evidence==="DEMO"?("spread "+num(ev.avg_entry_spread_price)+" · entry slip "+num(ev.avg_entry_slippage_price)+
        " · exit slip "+num(ev.avg_exit_slippage_price)+" (price units, n="+ev.spread_sample+")"):
        ("sim spread "+num(ev.simulated_spread_cost_total)+" · sim slippage "+num(ev.simulated_slippage_cost_total)),"num");
      cell(tr,r.most_recent_executed_trade?time(r.most_recent_executed_trade.entry_time_utc):"—");
    }
    cell(tr,fmt(r.open_positions),"num");
    cell(tr,r.unrealized_pnl!==null&&r.unrealized_pnl!==undefined?signed(r.unrealized_pnl):(r.open_positions?(r.unrealized_note||"N/A"):"—"),"num");
    return tr;
  });
  const un=s.unattributed||{};const um=un.metrics||{};
  const utr=el("tr");cell(utr,"");cell(utr,"");const ul=el("span","UNATTRIBUTED (not a strategy)","text-warn");cell(utr,ul);
  cell(utr,"—");cell(utr,"no durable chain");
  for(let i=0;i<5;i++)cell(utr,"—","num");cell(utr,fmt(um.closed_trades),"num");
  if(!um.closed_trades){const td=cell(utr,"NO CLOSED TRADES","na");td.colSpan=17;}
  else{cell(utr,um.wins+" / "+um.losses+" / "+um.breakevens,"num");cell(utr,pct(um.win_rate),"num");
    cell(utr,signed(um.gross_profit_of_winners),"num");cell(utr,signed(um.gross_loss_of_losers),"num");
    cell(utr,signed(um.gross_pnl),"num");cell(utr,signed(um.commission),"num");cell(utr,signed(um.fee),"num");cell(utr,signed(um.swap),"num");
    cell(utr,signed(labCosts(um)),"num");cell(utr,signed(um.net_pnl),"num");for(let i=0;i<6;i++)cell(utr,"—","num");cell(utr,"—");}
  cell(utr,"—","num");cell(utr,"—","num");rows.push(utr);
  box.appendChild(labGrossNet(s));
  box.appendChild(el("div","Winning and losing strategies · "+s.evidence+" evidence","mini-title"));
  box.appendChild(gridTable(heads,rows,{nowrap:true}));
  box.appendChild(el("p","Metrics use CLOSED trades only (an entry is never a completed trade). Net = gross + commission + fees + swap, "+
    "Gross profit / loss (winners / losers) sum the NET realized result of winning / losing closed trades (profit factor = profit / |loss|). "+
    "Unrealized is the broker's live floating P&L of still-open positions, shown beside and never inside realized results. "+
    "all broker-recorded for DEMO. Spread and slippage are already inside DEMO broker profit, so they are shown as evidence only. "+
    "Profit factor with no losses is undefined, never infinite. Max drawdown is on the closed-trade sequence. "+
    "Rows stay in registry order and are never ranked: small samples cannot support a ranking.","lab-note"));
  if(s.evidence==="DEMO")box.appendChild(el("div",s.funnel_note,"safeguard"));
  box.appendChild(el("div","Retired permanently (never executable, never shown as candidates): "+s.retired_strategies.join(", "),"safeguard"));
  const grid=el("div",null,"chart-grid");
  const series=s.active_strategy_keys.map(k=>({key:k,label:k,color:labColor(k),
    points:(s.curves[k]||[]).map(p=>({x:p.t,y:p.cum_net,tip:time(p.t)+" · cumulative "+money(p.cum_net)+" "+mcur()}))})).filter(x=>x.points.length);
  const curveTable=gridTable(["Strategy",["Closed","num"],["Final cumulative net","num"]],series.map(x=>{const tr=el("tr");cell(tr,x.label);
    cell(tr,String(x.points.length),"num");cell(tr,signed(x.points[x.points.length-1].y),"num");return tr;}));
  grid.appendChild(chartCard("Cumulative realized net P&L by strategy ("+mcur()+")",lineChart(series,{aria:"cumulative net P&L"}),curveTable));
  const gc=s.strategies.filter(r=>r.closed_trades).flatMap(r=>[
    {label:r.strategy_key+" gross",value:r.gross_pnl||0},
    {label:r.strategy_key+" costs",value:(r.commission||0)+(r.fee||0)+(r.swap||0)}]);
  grid.appendChild(chartCard("Gross P&L versus recorded costs ("+mcur()+")",barChart(gc,{aria:"gross versus costs"}),null,
    "Costs = commission + fees + swap as recorded. When costs outweigh gross results, frequent trading loses money even if signals are sometimes right."));
  const bd=s.breakdowns||{};
  const groupChart=(title,rowsIn,key)=>{
    const items=(rowsIn||[]).map(r=>({label:r[key]+" (n="+r.closed_trades+")",value:r.net_pnl,
      tip:r[key]+": net "+money(r.net_pnl)+" "+mcur()+", "+r.wins+"W/"+r.losses+"L of "+r.closed_trades}));
    const tbl=gridTable([key,["Closed","num"],["Wins","num"],["Losses","num"],["Gross","num"],["Costs","num"],["Net","num"]],
      (rowsIn||[]).map(r=>{const tr=el("tr");cell(tr,String(r[key]));cell(tr,fmt(r.closed_trades),"num");cell(tr,fmt(r.wins),"num");
        cell(tr,fmt(r.losses),"num");cell(tr,signed(r.gross_pnl),"num");cell(tr,signed(r.costs),"num");cell(tr,signed(r.net_pnl),"num");return tr;}));
    return chartCard(title+" ("+mcur()+")",barChart(items,{aria:title}),tbl);
  };
  grid.appendChild(groupChart("Net result by strategy, incl. unattributed",bd.by_strategy,"strategy_key"));
  grid.appendChild(groupChart("Net result by symbol",bd.by_symbol,"symbol"));
  grid.appendChild(groupChart("Net result by regime",bd.by_regime,"regime"));
  grid.appendChild(groupChart("Net result by direction",bd.by_direction,"direction"));
  grid.appendChild(groupChart("Net result by session",bd.by_session,"session"));
  grid.appendChild(groupChart("Net result by exit reason",bd.by_exit_reason,"exit_reason"));
  const freq=(bd.by_day||[]).map(d=>({label:d.day,value:d.closed_trades,valueLabel:String(d.closed_trades),color:"var(--s1)",
    tip:d.day+": "+d.closed_trades+" closed, net "+money(d.net_pnl)+" "+mcur()}));
  grid.appendChild(chartCard("Trade frequency (closed trades per UTC day)",barChart(freq,{aria:"trade frequency"}),
    gridTable(["Day",["Closed","num"],["Net","num"]],(bd.by_day||[]).map(d=>{const tr=el("tr");cell(tr,d.day);cell(tr,String(d.closed_trades),"num");
      cell(tr,signed(d.net_pnl),"num");return tr;}))));
  const dist=(title,arr)=>chartCard(title,barChart((arr||[]).map(b=>({label:b.bucket,value:b.count,valueLabel:String(b.count),color:"var(--s1)"})),
    {aria:title}),gridTable(["Bucket",["Trades","num"]],(arr||[]).map(b=>{const tr=el("tr");cell(tr,b.bucket);cell(tr,String(b.count),"num");return tr;})));
  grid.appendChild(dist("Win/loss distribution (net per closed trade, "+mcur()+")",bd.net_distribution));
  grid.appendChild(dist("R-multiple distribution (trades with recorded initial risk)",bd.r_distribution));
  if(s.evidence==="DEMO"){
    const conv=s.strategies.map(r=>{const f=r.funnel||{};const tr=el("tr");cell(tr,r.strategy_key);
      ["signals","signals_rejected","selected_proposals","entry_allowed_chains","entry_blocked_chains","orders_created","orders_submitted","orders_filled"]
        .forEach(k=>cell(tr,fmt(f[k]),"num"));
      cell(tr,pct(f.signal_to_order_conversion),"num");cell(tr,pct(f.order_to_fill_conversion),"num");return tr;});
    const c=el("div",null,"chart-card");c.style.gridColumn="1/-1";c.appendChild(el("h4","Signal → order → fill conversion (DEMO runtime chains)"));
    c.appendChild(gridTable(["Strategy",["Signals","num"],["Rejected","num"],["Selected","num"],["Allowed","num"],["Blocked","num"],
      ["Order rows","num"],["Submitted","num"],["Filled","num"],["Signal→order","num"],["Order→fill","num"]],conv));
    c.appendChild(el("p","Strategies that signal but never reach SUBMITTED are producing hypotheses the gates reject; "+
      "Strategy detail → why-no-trade lists the recorded reasons.","lab-note"));
    grid.appendChild(c);
  }
  box.appendChild(grid);
  return box;
}
function kvTable(obj){const rows=Object.entries(obj||{}).map(([k,v])=>{const tr=el("tr");cell(tr,heading(k));
  cell(tr,/(_utc|_at)$/.test(k)&&typeof v==="number"?time(v):(v!==null&&typeof v==="object"?JSON.stringify(v):fmt(v)));return tr;});
  return gridTable(["Field","Value"],rows);}
function tradesTable(list,evidence){
  const heads=["Trade / position","Strategy","Source","Symbol","Dir","Status","Regime","Entry","Exit",["Closed / entry vol","num"],
    ["Gross","num"],["Costs","num"],["Net (realized)","num"],["R","num"],"Exit reason"];
  const rows=list.map(t=>{const tr=el("tr",null,"clickable");tr.tabIndex=0;
    const open=()=>labOpenTrade(evidence||t.evidence,t.trade_id);
    tr.addEventListener("click",open);tr.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open();}});
    cell(tr,t.trade_id+(t.run_id?" · "+t.run_id:""));
    const sk=el("span",t.strategy_key||"UNATTRIBUTED");if(!t.strategy_key)sk.className="text-warn";cell(tr,sk);
    cell(tr,t.source_class);cell(tr,fmt(t.symbol));cell(tr,fmt(t.direction));cell(tr,t.status);cell(tr,fmt(t.regime));
    cell(tr,time(t.entry_time_utc));cell(tr,time(t.exit_time_utc));cell(tr,num(t.closed_volume)+" / "+num(t.entry_volume),"num");
    cell(tr,signed(t.gross_pnl,t.currency),"num");cell(tr,signed(t.costs,t.currency),"num");cell(tr,signed(t.realized_net_pnl,t.currency),"num");
    cell(tr,num(t.realized_r),"num");cell(tr,fmt(t.exit_reason));return tr;});
  return gridTable(heads,rows);
}
function labDetail(){
  const box=el("div",null,"lab");const s=LAB.summary;
  const bar=el("div",null,"lab-bar");const lab=el("label","Strategy");const sel=el("select");
  [...s.active_strategy_keys,...s.retired_strategies].forEach(k=>{const o=el("option",k+(s.retired_strategies.includes(k)?" (retired)":""));o.value=k;sel.appendChild(o);});
  sel.value=LAB.detailKey;sel.addEventListener("change",()=>{LAB.detailKey=sel.value;LAB.detail=null;labLoad(true);});
  lab.appendChild(sel);bar.appendChild(lab);box.appendChild(bar);
  const d=LAB.detail;
  if(!d||d.strategy_key!==LAB.detailKey){box.appendChild(el("p","Loading strategy detail…","empty"));return box;}
  const src=d.source;
  box.appendChild(el("h3",d.strategy_key+" · "+d.registration_status));
  if(!src){box.appendChild(el("div","Permanently retired: no source module is registered and it can never be executed.","safeguard"));}
  else{
    box.appendChild(el("p",src.description,"lab-note"));
    const st=el("div",null,"stat-grid");
    st.appendChild(stat("Version","v"+src.version,src.class_name));
    st.appendChild(stat("Eligible regimes",src.regime_gate&&src.regime_gate.eligible?src.regime_gate.eligible.join(", "):"see entry rules",
      src.regime_gate&&src.regime_gate.rule?src.regime_gate.rule:""));
    st.appendChild(stat("ATR stop / target",num(src.stop_atr_multiple)+" / "+num(src.target_atr_multiple)+" × ATR","price distances, not money"));
    st.appendChild(stat("Required confidence",num(src.min_confidence),"minimum raw confidence"));
    st.appendChild(stat("Expected duration",dur(src.expected_duration_seconds),"strategy's own estimate"));
    box.appendChild(st);
    box.appendChild(el("div","Current parameters (read from the registered instance)","mini-title"));box.appendChild(kvTable(src.current_parameters));
    const dd=el("details");dd.appendChild(el("summary","Actual entry rules — evaluate() source, verbatim"));
    dd.appendChild(el("pre",src.entry_rules_source||"source unavailable","src"));box.appendChild(dd);
  }
  const m=d.demo_metrics||{};
  const st2=el("div",null,"stat-grid");
  st2.appendChild(stat("DEMO closed trades",m.closed_trades?fmt(m.closed_trades):"NO CLOSED TRADES",""));
  const net=stat("DEMO net realized","","");net.querySelector(".value").appendChild(signed(m.net_pnl,d.currency));st2.appendChild(net);
  st2.appendChild(stat("Win rate",pct(m.win_rate),m.closed_trades?m.wins+"W / "+m.losses+"L / "+m.breakevens+"BE":""));
  st2.appendChild(stat("Open positions",fmt((d.open_positions||[]).length),"broker-confirmed"));
  box.appendChild(st2);
  const ev=(title,obj)=>{box.appendChild(el("div",title,"mini-title"));
    box.appendChild(obj?kvTable(Object.fromEntries(Object.entries(obj).filter(([k])=>k!=="payload"))):el("p","None recorded.","empty"));
    if(obj&&obj.payload){const x=el("details");x.appendChild(el("summary","Recorded payload"));x.appendChild(render(obj.payload,1));box.appendChild(x);}};
  ev("Latest genuine evaluation (entry_decisions)",d.latest_evaluation);
  ev("Latest proposed signal (runtime chain)",d.latest_signal);
  ev("Last selected signal",d.latest_selected);
  box.appendChild(el("div","Latest broker-confirmed trade","mini-title"));
  box.appendChild(d.latest_broker_confirmed_trade?tradesTable([d.latest_broker_confirmed_trade],"DEMO"):
    el("p","None: this strategy has no broker-confirmed DEMO trade.","empty"));
  box.appendChild(el("div","Current open positions","mini-title"));
  box.appendChild((d.open_positions||[]).length?tradesTable(d.open_positions,"DEMO"):el("p","None.","empty"));
  box.appendChild(el("div","Recent closed trades","mini-title"));
  box.appendChild((d.recent_closed_trades||[]).length?tradesTable(d.recent_closed_trades,"DEMO"):el("p","NO CLOSED TRADES.","empty"));
  box.appendChild(el("div","Why-no-trade history (latest 25 non-filled decisions)","mini-title"));
  box.appendChild((d.why_no_trade||[]).length?gridTable(["Decided","Mode","Symbol","Stage","Decision","Reason"],d.why_no_trade.map(w=>{const tr=el("tr");
    cell(tr,time(w.decided_at_utc));cell(tr,fmt(w.mode));cell(tr,fmt(w.symbol));cell(tr,fmt(w.stage));cell(tr,fmt(w.decision));
    cell(tr,fmt(w.reason));return tr;})):el("p","No recorded decisions for this strategy.","empty"));
  box.appendChild(labLifecycle());
  return box;
}
function labTrades(unattributed){
  const box=el("div",null,"lab");const t=LAB.trades;const s=LAB.summary;
  if(unattributed&&s){
    const u=s.unattributed||{};box.appendChild(el("div","Unattributed trade records by source ("+s.evidence+")","mini-title"));
    box.appendChild(gridTable(["Source class",["Records","num"],["Closed","num"],["Realized net","num"],["Net of all deals","num"]],
      Object.entries(u.by_source_class||{}).map(([k,v])=>{const tr=el("tr");cell(tr,k);cell(tr,fmt(v.trade_records),"num");cell(tr,fmt(v.closed),"num");
        cell(tr,signed(v.realized_net_pnl),"num");cell(tr,signed(v.net_pnl_all_deals),"num");return tr;})));
    box.appendChild(el("p","ASN_UNATTRIBUTED = this runtime's own trade whose durable chain is incomplete. EXTERNAL_EXPERT = another expert "+
      "adviser's magic number. MANUAL = broker reason CLIENT/MOBILE/WEB. UNKNOWN_SOURCE = magic 0 with no broker reason recorded. "+
      "None of these is ever guessed onto a strategy.","lab-note"));
  }
  const bar=el("div",null,"lab-bar");const l=el("label","Search (ticket, strategy, symbol, exit reason, chain, run)");
  const inp=el("input");inp.type="search";inp.className="search";inp.value=LAB.q;
  let timer=null;inp.addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>{LAB.q=inp.value;LAB.page=1;labLoad(true);},400);});
  l.appendChild(inp);bar.appendChild(l);box.appendChild(bar);
  if(!t){box.appendChild(el("p","Loading trades…","empty"));return box;}
  box.appendChild(el("p",fmt(t.total)+" matching trade record(s). Select a row (click or Enter) for its complete traceable lifecycle.","lab-note"));
  box.appendChild(t.trades.length?tradesTable(t.trades,t.evidence):el("p","No trades match the current filters.","empty"));
  const pg=el("div",null,"pager");
  const mk=(label,p,dis)=>{const b=el("button",label);b.type="button";b.className="secondary-button";b.disabled=dis;
    b.addEventListener("click",()=>{LAB.page=p;labLoad(true);});pg.appendChild(b);};
  mk("« First",1,t.page<=1);mk("‹ Prev",t.page-1,t.page<=1);pg.appendChild(el("span","Page "+t.page+" of "+t.pages,"tiny"));
  mk("Next ›",t.page+1,t.page>=t.pages);mk("Last »",t.pages,t.page>=t.pages);box.appendChild(pg);
  box.appendChild(labLifecycle());
  return box;
}
function labLifecycle(){
  const box=el("div",null,"chart-card");box.id="lab-lifecycle";const x=LAB.trade;
  if(!x){box.classList.add("hidden");return box;}
  const head=el("div",null,"lab-bar");head.appendChild(el("h4","Trade lifecycle · "+fmt(x.id||(x.trade&&x.trade.trade_id))));
  const close=el("button","Close");close.type="button";close.className="secondary-button";close.addEventListener("click",()=>{LAB.trade=null;labRender();});
  head.appendChild(close);box.appendChild(head);
  if(x.loading){box.appendChild(el("p","Loading lifecycle…","empty"));return box;}
  if(x.error){box.appendChild(el("p",x.error,"error"));return box;}
  const t=x.trade;
  const st=el("div",null,"stat-grid");
  st.appendChild(stat("Strategy",t.strategy_key||"UNATTRIBUTED",t.source_class+(t.strategy_version?" · v"+t.strategy_version:"")));
  st.appendChild(stat("Status",t.status,fmt(t.symbol)+" "+fmt(t.direction)));
  const n=stat("Net realized","","");n.querySelector(".value").appendChild(signed(t.realized_net_pnl,t.currency));st.appendChild(n);
  st.appendChild(stat("Realized R",num(t.realized_r),"initial risk "+num(t.initial_monetary_risk)));
  st.appendChild(stat("Entry / exit price",num(t.entry_price,5)+" / "+num(t.exit_price,5),"volume "+num(t.entry_volume)));
  st.appendChild(stat("Initial SL / TP",num(t.initial_stop_loss,5)+" / "+num(t.initial_take_profit,5),"as requested"));
  st.appendChild(stat("Exit",fmt(t.exit_reason),fmt(t.exit_reason_evidence)));
  box.appendChild(st);
  if(t.attribution_checks&&t.attribution_checks.length){
    box.appendChild(el("div","Attribution evidence (every link must pass)","mini-title"));
    box.appendChild(gridTable(["Check","Result","Detail"],t.attribution_checks.map(c=>{const tr=el("tr");cell(tr,c.check);
      cell(tr,el("span",c.passed?"PASS":"FAIL","badge "+(c.passed?"ok":"bad")));cell(tr,fmt(c.detail));return tr;})));
  }
  const sec=(title,val)=>{if(val===undefined)return;const d=el("details");d.appendChild(el("summary",title));
    const inner=el("div",null,"nested");inner.appendChild(val===null||(Array.isArray(val)&&!val.length)?el("p","None recorded.","empty"):render(val,1));
    d.appendChild(inner);box.appendChild(d);};
  sec("1 · Original signal (regime, features, confidence)",x.signal);
  sec("2 · Selection decision",x.selection);
  sec("Other strategies' signals on the same bar",x.other_signals_in_chain);
  sec("3 · Permission checks",x.permission_checks);
  sec("Recorded entry decisions",x.entry_decisions);
  sec("4 · Expected and observed execution costs",x.execution_cost_evidence);
  sec("5 · Local order",x.local_order);sec("Order state transitions",x.order_transitions);
  sec("6 · Order journal events",x.order_events);
  sec("7 · Broker order(s) (broker-history import)",x.broker_orders);
  sec("8 · Broker deals (entry and exit fills)",x.broker_deals);
  sec("9 · Position management state (initial risk, exit decision/fill R, slippage)",x.position_management_state);
  sec("10 · Management actions (stop moves, close, reconciliation)",x.management_actions);
  sec("Position reviews",x.position_reviews);
  sec("Advisory evidence (ML/RAG; no authority)",x.advisory_evidence);
  sec("11 · Accounting",x.accounting);
  sec("Simulation record",x.simulation_record);sec("Research run provenance",x.run);
  return box;
}
function labCompare(){
  const box=el("div",null,"lab");const s=LAB.summary;
  const keys=LAB.compare.filter(k=>s.strategies.some(r=>r.strategy_key===k)).slice(0,3);
  if(!keys.length){box.appendChild(el("p","Tick up to three “Compare” boxes in the Comparison table.","empty"));return box;}
  const rows=keys.map(k=>s.strategies.find(r=>r.strategy_key===k));
  const metric=(label,fn)=>{const tr=el("tr");cell(tr,label);rows.forEach(r=>cell(tr,fn(r),"num"));return tr;};
  const f=(r,k)=>s.evidence==="DEMO"?fmt((r.funnel||{})[k]):"N/A";
  const trs=[
    metric("Closed trades (sample size)",r=>r.closed_trades?String(r.closed_trades):"NO CLOSED TRADES"),
    metric("Signals / selected / submitted / filled",r=>[f(r,"signals"),f(r,"selected_proposals"),f(r,"orders_submitted"),f(r,"orders_filled")].join(" / ")),
    metric("Wins / losses / breakevens",r=>r.wins+" / "+r.losses+" / "+r.breakevens),metric("Win rate",r=>pct(r.win_rate)),
    metric("Gross P&L",r=>signed(r.gross_pnl)),
    metric("Commission + fees + swap",r=>r.closed_trades?signed((r.commission||0)+(r.fee||0)+(r.swap||0)):"N/A"),
    metric("Net P&L",r=>signed(r.net_pnl)),metric("Profit factor",r=>r.profit_factor!==null?num(r.profit_factor):fmt(r.profit_factor_note)),
    metric("Average R (sample)",r=>r.avg_r!==null?num(r.avg_r)+" ("+r.r_sample+")":"N/A"),
    metric("Expectancy per trade",r=>signed(r.expectancy_per_trade)),metric("Average hold",r=>dur(r.avg_holding_seconds)),
    metric("Max drawdown (closed-trade basis)",r=>r.max_drawdown_closed_trade_basis===null?"N/A":signed(-r.max_drawdown_closed_trade_basis)),
  ];
  box.appendChild(gridTable(["Metric",...rows.map(r=>[r.strategy_key,"num"])],trs,{caption:s.evidence+" evidence · identical filters for every column"}));
  const series=keys.map(k=>({key:k,label:k,color:labColor(k),points:(s.curves[k]||[]).map(p=>({x:p.t,y:p.cum_net,
    tip:time(p.t)+" · cumulative "+money(p.cum_net)+" "+mcur()}))}));
  box.appendChild(chartCard("Cumulative realized net P&L, side by side ("+mcur()+")",lineChart(series,{aria:"compare cumulative net"}),
    gridTable(["Strategy",["Closed","num"],["Final","num"]],series.map(x=>{const tr=el("tr");cell(tr,x.label);cell(tr,String(x.points.length),"num");
      cell(tr,x.points.length?signed(x.points[x.points.length-1].y):"N/A","num");return tr;}))));
  box.appendChild(el("p","A strategy with few closed trades has an insufficient sample: a positive figure there is not evidence of an edge.","safeguard"));
  return box;
}
function labReconciliation(){
  const box=el("div",null,"lab");const s=LAB.summary;const r=s.reconciliation;
  if(!r){box.appendChild(el("p","Reconciliation applies to DEMO broker evidence only. PAPER and BACKTEST are simulations with no broker-deal population.","empty"));return box;}
  const st=el("div",null,"stat-grid");
  st.appendChild(stat("Reconciles",r.reconciles?"YES":"NO: investigate",r.reconciles?"ledger = independent SQL sum":"see discrepancies below"));
  const a=stat("Population net (SQL)","","");a.querySelector(".value").appendChild(signed(r.population_net_sql,r.currency));st.appendChild(a);
  const b=stat("Population net (ledger)","","");b.querySelector(".value").appendChild(signed(r.population_net_ledger,r.currency));st.appendChild(b);
  st.appendChild(stat("Deals (SQL / ledger)",fmt(r.population_deal_count_sql)+" / "+fmt(r.population_deal_count_ledger),"account "+fmt(r.login)));
  st.appendChild(stat("Money discrepancy",num(r.ledger_vs_sql_money_discrepancy,6),r.currency||""));
  st.appendChild(stat("Closed-count check",r.closed_count_check.matches?"MATCHES":"MISMATCH",
    fmt(r.closed_count_check.attributed_closed_positions)+" attributed closed vs "+fmt(r.closed_count_check.local_positions_marked_closed)+" local CLOSED rows"));
  box.appendChild(st);
  box.appendChild(el("p",r.population,"lab-note"));box.appendChild(el("div",r.formula,"safeguard"));
  const rows=Object.entries(r.by_source_class).map(([k,v])=>{const tr=el("tr");cell(tr,k);cell(tr,fmt(v.positions),"num");
    cell(tr,fmt(v.closed),"num");cell(tr,fmt(v.partially_closed),"num");cell(tr,fmt(v.open),"num");cell(tr,fmt(v.other_status),"num");
    cell(tr,signed(v.realized,r.currency),"num");cell(tr,signed(v.open_volume_entry_costs,r.currency),"num");cell(tr,signed(v.net_all_deals,r.currency),"num");return tr;});
  const nt=el("tr");cell(nt,"Non-trade deals (deposits, balance/credit adjustments)");cell(nt,fmt(r.non_trade_deal_count),"num");
  for(let i=0;i<6;i++)cell(nt,"—","num");cell(nt,signed(r.non_trade_deals_net,r.currency),"num");rows.push(nt);
  const ot=el("tr");cell(ot,"Trade deals without a position id");cell(ot,fmt(r.trade_deals_without_position_count),"num");
  for(let i=0;i<6;i++)cell(ot,"—","num");cell(ot,signed(r.trade_deals_without_position_net,r.currency),"num");rows.push(ot);
  const tot=el("tr");cell(tot,"TOTAL (must equal the SQL population)");for(let i=0;i<7;i++)cell(tot,"");
  cell(tot,signed(r.population_net_ledger,r.currency),"num");rows.push(tot);
  box.appendChild(gridTable(["Source class",["Positions","num"],["Closed","num"],["Partial","num"],["Open","num"],["Other","num"],
    ["Realized","num"],["Entry costs on open volume","num"],["Net of all deals","num"]],rows));
  box.appendChild(el("div","Broker-history vs runtime-record discrepancies ("+r.deal_source_discrepancies.length+")","mini-title"));
  box.appendChild(r.deal_source_discrepancies.length?table(r.deal_source_discrepancies):el("p","None: every deal present in both sources agrees.","empty"));
  if(r.local_positions_without_deals.length){box.appendChild(el("div","Local positions with no broker deal in the population","mini-title"));
    box.appendChild(table(r.local_positions_without_deals));}
  box.appendChild(el("div","Broker-history coverage","mini-title"));box.appendChild(kvTable(s.broker_history_coverage||{}));
  return box;
}
function labShortlist(){
  const box=el("div",null,"lab");const s=LAB.summary;
  box.appendChild(el("div","Human review shortlist: this list lives only in this browser. It never changes live execution, "+
    "never enables, disables or promotes a strategy, and never changes risk. Any strategy configuration change requires a separately "+
    "reviewed code/config change, tests and an operator-approved controlled deployment.","safeguard"));
  if(!LAB.shortlist.length){box.appendChild(el("p","Tick “Shortlist” boxes in the Comparison table to build a research-review list.","empty"));return box;}
  const rows=LAB.shortlist.map(k=>{const r=s.strategies.find(x=>x.strategy_key===k);const tr=el("tr");cell(tr,k);
    const rm=el("button","Remove");rm.type="button";rm.className="secondary-button";
    rm.addEventListener("click",()=>{LAB.shortlist=LAB.shortlist.filter(x=>x!==k);labSave("asn-lab-shortlist",LAB.shortlist);labRender();});
    if(!r){const td=cell(tr,"not in this evidence set","na");td.colSpan=4;cell(tr,rm);return tr;}
    cell(tr,r.closed_trades?String(r.closed_trades):"NO CLOSED TRADES","num");cell(tr,pct(r.win_rate),"num");cell(tr,signed(r.net_pnl),"num");
    cell(tr,r.profit_factor!==null?num(r.profit_factor):fmt(r.profit_factor_note),"num");cell(tr,rm);return tr;});
  box.appendChild(gridTable(["Strategy",["Closed","num"],["Win rate","num"],["Net","num"],["Profit factor","num"],""],rows,
    {caption:s.evidence+" evidence under the current filters"}));
  const row=el("div",null,"lab-bar");
  const a=el("a","Export comparison CSV (current evidence and filters)");a.className="secondary-button";a.style.textDecoration="none";
  a.href="/api/strategy-lab/export.csv?"+labQuery({keys:LAB.shortlist.join(",")});a.setAttribute("download","");row.appendChild(a);
  const j=el("button","Export shortlist JSON");j.type="button";j.className="secondary-button";
  j.addEventListener("click",()=>downloadText("strategy-review-shortlist.json",JSON.stringify({exported_at:new Date().toISOString(),
    evidence:s.evidence,filters:s.filters,currency:s.currency,strategies:s.strategies.filter(x=>LAB.shortlist.includes(x.strategy_key))},null,2)));
  row.appendChild(j);box.appendChild(row);
  return box;
}
function labRender(){
  const root=labRoot();if(!root)return;
  const y=window.scrollY;const frag=el("div",null,"lab");
  frag.appendChild(labControls());
  const s=LAB.summary;
  if(LAB.evidence==="RESEARCH"){frag.appendChild(labResearch());}
  else if(LAB.sub==="readiness"){
    const mp=data&&data.panels?data.panels.multi_position:null;
    frag.appendChild(!mp?el("p","Waiting for the dashboard feed…","empty"):
      (mp.status==="NO_DATA"||mp.status==="UNAVAILABLE")?el("p",fmt(mp.status)+": "+fmt(mp.detail),"empty"):
      multiPositionPanel(mp));
  }
  else if(!s){frag.appendChild(el("p",LAB.error?("Strategy Lab unavailable: "+LAB.error):"Loading Strategy Lab evidence…",LAB.error?"error":"empty"));}
  else if(LAB.sub==="comparison")frag.appendChild(labComparison(s));
  else if(LAB.sub==="detail")frag.appendChild(labDetail());
  else if(LAB.sub==="compare")frag.appendChild(labCompare());
  else if(LAB.sub==="trades")frag.appendChild(labTrades(false));
  else if(LAB.sub==="unattributed")frag.appendChild(labTrades(true));
  else if(LAB.sub==="reconciliation")frag.appendChild(labReconciliation());
  else if(LAB.sub==="shortlist")frag.appendChild(labShortlist());
  if(s&&LAB.evidence!=="RESEARCH"&&!["trades","unattributed","detail","readiness"].includes(LAB.sub))frag.appendChild(labLifecycle());
  frag.appendChild(el("div","Strategy Lab is review-only. It reads the local database; it cannot place, modify or close orders, "+
    "change risk, touch the kill switch, or activate, disable or promote any strategy.","footer"));
  root.replaceChildren(frag);window.scrollTo(0,y);
}
/* refresh while visible at most every 20 s; the server only recomputes when the database changed */
setInterval(()=>{if(current==="strategy_lab"&&!LAB.loading&&!LAB.trade&&Date.now()-LAB.lastFetch>20000&&!document.hidden)labLoad(false);},5000);
function showView(){
  const group=GROUPS.find(g=>g.id===current)||GROUPS[0];
  const isLab=current==="strategy_lab";
  ["kpis","panel-toolbar","panels"].forEach(id=>document.getElementById(id).classList.toggle("hidden",isLab));
  labRoot().classList.toggle("hidden",!isLab);
  document.getElementById("view-title").textContent=group.label;
  document.getElementById("view-desc").textContent=group.desc;
  document.getElementById("section-title").textContent=group.label+" panels";
  nav.querySelectorAll("button").forEach(n=>{
    if(n.dataset.view===current)n.setAttribute("aria-current","page");
    else n.removeAttribute("aria-current");
  });
  const q=document.getElementById("search").value.trim().toLowerCase();
  Object.entries(cards).forEach(([name,c])=>{
    const matches=!q||FRIENDLY[name].toLowerCase().includes(q)||name.includes(q);
    c.section.classList.toggle("hidden",!matches||(current!=="all"&&!group.panels.includes(name)&&!q));
  });
}
function paint(payload){
  if(!payload||payload.status==="UNAVAILABLE"){
    const msg=payload&&payload.detail?payload.detail:"Dashboard database unavailable";
    const a=document.getElementById("alert");a.textContent=msg;a.className="warning-banner visible";
    pill("feed","Dashboard: data unavailable","bad");return;
  }
  if(data&&Number(payload.generated_at_utc||0)<Number(data.generated_at_utc||0))return;
  data=payload;paintSummary();
  Object.entries(cards).forEach(([name,c])=>{
    const val=(data.panels||{})[name];
    if(!val)return;
    const serial=JSON.stringify(val);
    if(lastContent[name]===serial)return;
    lastContent[name]=serial;
    const scrollTop=c.body.scrollTop;
    c.body.replaceChildren(panelBody(name,val));
    c.body.scrollTop=scrollTop;
    c.source.textContent=val.status==="UNAVAILABLE"?"Unavailable":
      val.status==="NO_DATA"?"Awaiting data":
      name==="market"?"Sampled every 5s":"From the local database";
  });
  showView();
}
async function poll(){
  if(polling)return;polling=true;
  try{
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
    let response;try{response=await fetch("/api/panels",{cache:"no-store",signal:controller.signal});}
    finally{clearTimeout(timer);}
    if(!response.ok)throw Error("HTTP "+response.status);
    feedState=ws&&ws.readyState===1?"WS":"POLL";paint(await response.json());
  }catch(err){
    if(feedState!=="WS")feedState="OFFLINE";
    if(data)paintSummary();
    else{const a=document.getElementById("alert");a.textContent="Dashboard server unreachable: "+String(err);
      a.className="warning-banner visible";}
  }finally{polling=false;}
}
function startPolling(){
  if(pollHandle===null){poll();pollHandle=setInterval(poll,3000);}
}
function socket(){
  if(ws&&ws.readyState===1)return;
  try{ws=new WebSocket((location.protocol==="https:"?"wss://":"ws://")+location.host+"/ws");}
  catch(err){startPolling();scheduleRetry();return;}
  ws.onmessage=e=>{
    try{feedState="WS";paint(JSON.parse(e.data));if(pollHandle!==null){clearInterval(pollHandle);pollHandle=null;}}
    catch(err){startPolling();}
  };
  ws.onclose=()=>{feedState="POLL";startPolling();scheduleRetry();};
  ws.onerror=()=>{ws.close();};
}
function scheduleRetry(){if(retry!==null)return;retry=setTimeout(()=>{retry=null;socket();},5000);}
document.getElementById("refresh").addEventListener("click",poll);
document.getElementById("search").addEventListener("input",showView);
const initialTheme=localStorage.getItem("asn-theme");
if(initialTheme==="light"||initialTheme==="dark")document.documentElement.dataset.theme=initialTheme;
document.getElementById("theme").addEventListener("click",()=>{
  const next=document.documentElement.dataset.theme==="light"?"dark":"light";
  document.documentElement.dataset.theme=next;localStorage.setItem("asn-theme",next);
});
showView();startPolling();socket();
setInterval(()=>{
  if(data)paintSummary();
  if(ws&&ws.readyState===1&&data&&Date.now()/1000-Number(data.generated_at_utc||0)>12)
    ws.close();
},5000);
</script>
</body>
</html>
"""