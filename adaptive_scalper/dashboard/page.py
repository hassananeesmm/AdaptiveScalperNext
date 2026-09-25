"""Local, dependency-free, observer-only Windows dashboard.

No outside assets or dynamic HTML insertion. All broker-derived values are
snapshots written by the runtime; this browser never opens MT5 or writes to
the database. Designed for Windows laptop sizes, scaling and touch input.
"""

PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
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
button,input{font:inherit}button{cursor:pointer}
button:focus-visible,input:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.shell{min-height:100vh;display:grid;grid-template-columns:222px minmax(0,1fr)}
.sidebar{background:var(--side);border-right:1px solid var(--line);padding:18px 12px;position:sticky;top:0;height:100vh;overflow-y:auto}
.brand{display:flex;gap:11px;align-items:center;padding:5px 8px 20px}
.logo{background:linear-gradient(135deg,#2c81c7,#75c5db);color:#061629;font-weight:900;
  width:36px;height:36px;border-radius:11px;display:grid;place-items:center}
.brand-title{font-size:15px;font-weight:800;letter-spacing:.01em}
.brand-sub{font-size:11px;color:var(--muted)}
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
      <div><div class="brand-title">AdaptiveScalperNext</div><div class="brand-sub">Local operations monitor</div></div>
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
      <div class="toolbar">
        <h2 id="section-title">Live overview</h2>
        <input class="search" type="search" id="search" placeholder="Find a panel…" aria-label="Find a panel">
        <span class="tiny" id="data-age">No data</span>
      </div>
      <main class="panel-grid" id="panels" aria-live="off"></main>
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
   panels:["market","symbols","news","history"]},
  {id:"trading",label:"Trading",desc:"Positions, broker orders, simulated results and decision journal.",
   panels:["positions","orders","decisions","performance","costs"]},
  {id:"safety",label:"Safety & risk",desc:"Kill switch, active incidents, risk exposure and news health.",
   panels:["overview","risk","orders","events","news"]},
  {id:"research",label:"Research",desc:"Historical evaluations, observed outcomes, models and knowledge.",
   panels:["performance","research","learning","memory","knowledge","history"]},
  {id:"system",label:"System",desc:"Component status, runtime events, data freshness and broker status.",
   panels:["overview","components","market","events","history"]},
  {id:"all",label:"All panels",desc:"Every implemented dashboard panel.",
   panels:["overview","market","performance","components","symbols","positions","orders","decisions",
           "risk","news","events","costs","research","learning","memory","knowledge","history"]}
];
const WIDE = new Set(["market","positions","orders","performance","decisions","events","history"]);
const FRIENDLY = {
  overview:"System overview",market:"Live market & account",performance:"Observed performance",
  components:"Components",symbols:"Symbol resolution",positions:"Positions",orders:"Orders & reconciliation",
  decisions:"Decisions & reasons",risk:"Risk evidence",news:"Economic news",events:"Incidents & events",
  costs:"Execution costs",research:"Research history",learning:"Model observer",
  memory:"RAG memory",knowledge:"Knowledge bundle",history:"Historical coverage"
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
    spread_points:v.spread_points??null,status:v.status||"UNAVAILABLE",
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
function panelBody(name,raw){
  const p=raw||{};
  if(p.status==="UNAVAILABLE"||p.status==="NO_DATA")
    return el("p",fmt(p.status)+": "+fmt(p.detail),p.status==="UNAVAILABLE"?"error":"empty");
  if(name==="market")return marketPanel(p);
  if(name==="performance")return performancePanel(p);
  const copy=Object.assign({},p);delete copy.status;return render(copy,0);
}
let current="overview",data=null,feedState="CONNECTING",pollHandle=null,polling=false,ws=null,retry=null;
const nav=document.getElementById("nav"),cards={},lastContent={};
GROUPS.forEach(g=>{
  const b=el("button",g.label);b.type="button";b.dataset.view=g.id;
  b.addEventListener("click",()=>{current=g.id;document.getElementById("search").value="";showView();});
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
  ["Dangerous UNKNOWN","Active unresolved orders"],["Feed freshness","Runtime and broker data"]
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
  const live=mk.status==="OK"&&mk.age_seconds<=15&&acct.trade_mode==="DEMO";
  const runtimeFresh=rt.status==="RUNNING"||rt.status==="DEGRADED";
  const rtStatus=runtimeFresh?"RUNNING"+(rt.mode?" · "+rt.mode:""):fmt(rt.status);
  pill("runtime","Runtime: "+rtStatus,runtimeFresh?"good":"bad");
  pill("health","Health: "+fmt(ov.health),
    ov.health==="HEALTHY"&&runtimeFresh?"good":ov.health==="CRITICAL"?"bad":"warn");
  pill("kill","Kill switch: "+fmt(ks.status),ks.blocks_new_entries?"warn":"good");
  pill("feed",feedState==="WS"?"Dashboard: live":feedState==="POLL"?"Dashboard: polling":"Dashboard: offline",
    feedState==="WS"?"good":feedState==="POLL"?"warn":"bad");
  const curr=acct.currency||"";
  document.getElementById("kpi-0").textContent=live?money(acct.equity,curr):"—";
  const brokerPositions=mk.broker_positions;
  const floatValid=live&&Array.isArray(brokerPositions)&&brokerPositions.every(x=>typeof x.floating_pnl==="number");
  document.getElementById("kpi-1").textContent=floatValid?
    money(brokerPositions.reduce((sum,x)=>sum+x.floating_pnl,0),curr):"—";
  document.getElementById("kpi-2").textContent=typeof risk.demo_realized_pnl_today_utc==="number"?
    money(risk.demo_realized_pnl_today_utc,curr):"—";
  document.getElementById("kpi-3").textContent=live&&Array.isArray(brokerPositions)?
    fmt(brokerPositions.length):"—";
  document.getElementById("kpi-4").textContent=Array.isArray(ord.dangerous_unknown)?
    fmt(ord.dangerous_unknown.length):"—";
  document.getElementById("kpi-5").textContent=runtimeFresh&&live?"Fresh":runtimeFresh?"Broker stale":"Unavailable";
  const alarm=document.getElementById("alert"),issues=[];
  if(!runtimeFresh)issues.push("Runtime is not reporting a fresh running heartbeat.");
  if(!live)issues.push("Fresh verified MT5 DEMO telemetry is unavailable; displayed figures may be old.");
  if(Array.isArray(ord.dangerous_unknown)&&ord.dangerous_unknown.length)
    issues.push(String(ord.dangerous_unknown.length)+" dangerous UNKNOWN/PENDING_RECONCILIATION orders require investigation.");
  if(acct.trade_mode&&acct.trade_mode!=="DEMO")issues.push("NON-DEMO ACCOUNT DETECTED: broker mutations must remain blocked.");
  if(issues.length){alarm.textContent=issues.join(" ");alarm.className="warning-banner visible";}
  else{alarm.textContent="";alarm.className="warning-banner";}
  document.getElementById("data-age").textContent=
    "Snapshot "+(data.generated_at_utc?time(data.generated_at_utc):"unavailable")+
    " · Broker telemetry "+(mk.age_seconds===undefined?"unavailable":fmt(mk.age_seconds)+"s old");
  document.getElementById("clock").textContent="Your local time: "+new Date().toLocaleString();
}
function showView(){
  const group=GROUPS.find(g=>g.id===current)||GROUPS[0];
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