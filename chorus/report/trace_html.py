"""Self-contained HTML trace viewer (Phase 1 UI).

One file, no server, no build step: embeds the projected traces as JSON and
renders the three-region inspector specced in CHORUS_PHASE_1_BUILD.md —
left rail (trajectory list), center (span waterfall), right (attribute
inspector). Latency is always spatial (bar position + width); color encodes span
kind and status only; machine data is monospace. Handles the empty and
error/replay states.
"""

from __future__ import annotations

import json
from pathlib import Path

from chorus.trace.spans import Trace


def _trace_to_json(trace: Trace) -> dict:
    return {
        "trace_id": trace.trace_id,
        "run_id": trace.run_id,
        "trajectory_id": trace.trajectory_id,
        "outcome": trace.outcome,
        "replay": trace.replay,
        "total_ms": round(trace.total_ms, 2),
        "total_tokens": trace.total_tokens,
        "total_cost_usd": round(trace.total_cost_usd, 4),
        "spans": [
            {
                "span_id": s.span_id,
                "parent_id": s.parent_id,
                "name": s.name,
                "kind": s.kind,
                "depth": s.depth,
                "start_ms": round(s.start_ms, 2),
                "duration_ms": round(s.duration_ms, 2),
                "status": s.status,
                "attributes": {k: _jsonable(v) for k, v in s.attributes.items()},
            }
            for s in trace.spans
        ],
    }


def _jsonable(value: object) -> object:
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def render_traces_html(traces: list[Trace], *, run_id: str = "") -> str:
    data = json.dumps([_trace_to_json(t) for t in traces])
    run_label = run_id or (traces[0].run_id if traces else "")
    return _TEMPLATE.replace("__DATA__", data).replace("__RUN__", run_label)


def write_traces_html(traces: list[Trace], path: Path | str, *, run_id: str = "") -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_traces_html(traces, run_id=run_id), encoding="utf-8")
    return out


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Chorus trace viewer</title>
<style>
  :root {
    --bg:#0b1020; --panel:#0f1629; --panel2:#111a30; --line:#1e293b;
    --txt:#e2e8f0; --muted:#94a3b8; --dim:#64748b;
    --mono: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    --sans: ui-sans-serif,system-ui,Segoe UI,Roboto,sans-serif;
    --ok:#22c55e; --err:#ef4444; --warn:#f59e0b; --info:#38bdf8;
    --model:#a78bfa; --tool:#38bdf8; --run:#94a3b8; --step:#64748b;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt); font-family:var(--sans);
         font-size:13px; height:100vh; overflow:hidden; }
  .grid { display:grid; grid-template-columns:240px 1fr 320px; height:100vh; }
  .col { overflow:auto; height:100vh; }
  .rail { background:var(--panel); border-right:1px solid var(--line); }
  .center { background:var(--bg); }
  .inspector { background:var(--panel); border-left:1px solid var(--line); }
  .hd { padding:12px 14px; font-size:11px; letter-spacing:.08em; text-transform:uppercase;
        color:var(--muted); border-bottom:1px solid var(--line); position:sticky; top:0;
        background:inherit; z-index:2; }
  .traj { display:flex; align-items:center; gap:8px; padding:9px 14px; cursor:pointer;
          border-left:2px solid transparent; }
  .traj:hover { background:var(--panel2); }
  .traj.sel { background:var(--panel2); border-left-color:var(--info); }
  .dot { width:9px; height:9px; border-radius:50%; flex:none; }
  .traj .id { font-family:var(--mono); }
  .traj .dur { margin-left:auto; font-family:var(--mono); color:var(--dim); font-size:12px; }
  .ok{background:var(--ok)} .fail{background:var(--err)} .error{background:var(--warn)}
  .runhdr { display:flex; gap:18px; padding:10px 16px; border-bottom:1px solid var(--line);
            font-family:var(--mono); color:var(--muted); font-size:12px; align-items:center; }
  .runhdr b { color:var(--txt); }
  .replaybadge { color:var(--warn); border:1px solid var(--warn); border-radius:4px;
                 padding:1px 6px; font-size:11px; }
  .rows { padding:6px 0; }
  .row { display:grid; grid-template-columns:260px 1fr 150px; align-items:center;
         gap:10px; padding:3px 16px; cursor:pointer; border:1px solid transparent; }
  .row:hover { background:var(--panel2); }
  .row.sel { background:var(--panel2); border-color:var(--line); }
  .row.err { background:rgba(239,68,68,.08); }
  .nm { display:flex; align-items:center; gap:7px; font-size:12.5px; white-space:nowrap;
        overflow:hidden; text-overflow:ellipsis; }
  .ic { font-family:var(--mono); width:14px; text-align:center; flex:none; }
  .ic.model{color:var(--model)} .ic.tool{color:var(--tool)} .ic.run{color:var(--run)}
  .ic.step{color:var(--step)} .ic.contract{color:var(--muted)}
  .track { position:relative; height:14px; background:var(--panel); border-radius:3px; }
  .bar { position:absolute; top:0; height:14px; border-radius:3px; min-width:2px; opacity:.85; }
  .bar.model{background:var(--model)} .bar.tool{background:var(--tool)}
  .bar.run{background:var(--run)} .bar.step{background:#334155}
  .bar.err{background:var(--err); outline:1px solid #fecaca; opacity:1; }
  .tail { font-family:var(--mono); font-size:11px; color:var(--dim); text-align:right;
          white-space:nowrap; }
  .ins { padding:14px 16px; }
  .ins h3 { margin:0 0 2px; font-size:13px; }
  .ins .sub { color:var(--muted); font-family:var(--mono); font-size:11px; margin-bottom:14px; }
  .kv { margin:0 0 11px; }
  .kv .k { color:var(--muted); font-size:11px; font-family:var(--mono); }
  .kv .v { color:var(--txt); font-family:var(--mono); font-size:12.5px; word-break:break-word; }
  .kv .v.err { color:var(--err); }
  .empty { display:flex; height:100vh; align-items:center; justify-content:center;
           color:var(--muted); font-family:var(--mono); }
  .hint { color:var(--dim); }
</style>
</head>
<body>
<div id="app"></div>
<script>
const TRACES = __DATA__;
const RUN = "__RUN__";
const ICON = { run:"▢", step:"›", model:"✦", tool:"▤", contract:"✓" };
let sel = { traj:0, span:0 };

function fmtMs(ms){ return ms >= 1000 ? (ms/1000).toFixed(2)+"s" : ms.toFixed(0)+"ms"; }
function el(html){ const t=document.createElement("template"); t.innerHTML=html.trim(); return t.content.firstChild; }

function render(){
  const app = document.getElementById("app");
  app.innerHTML = "";
  if(!TRACES.length){
    app.appendChild(el(`<div class="empty">no runs yet &mdash; <span class="hint">&nbsp;chorus run &hellip;</span></div>`));
    return;
  }
  const grid = el(`<div class="grid"></div>`);
  grid.appendChild(renderRail());
  grid.appendChild(renderCenter());
  grid.appendChild(renderInspector());
  app.appendChild(grid);
}

function renderRail(){
  const rail = el(`<div class="col rail"></div>`);
  rail.appendChild(el(`<div class="hd">trajectories &middot; ${TRACES.length}</div>`));
  TRACES.forEach((t,i)=>{
    const cls = t.outcome==="pass"?"ok":(t.outcome==="error"?"error":"fail");
    const sl = i===sel.traj?" sel":"";
    const rep = t.replay?` <span class="ic">↻</span>`:"";
    const row = el(`<div class="traj${sl}"><span class="dot ${cls}"></span>`+
      `<span class="id">#${String(i+1).padStart(2,"0")}${rep}</span>`+
      `<span class="dur">${fmtMs(t.total_ms)}</span></div>`);
    row.onclick = ()=>{ sel={traj:i, span:0}; render(); };
    rail.appendChild(row);
  });
  return rail;
}

function renderCenter(){
  const t = TRACES[sel.traj];
  const col = el(`<div class="col center"></div>`);
  const rep = t.replay?`<span class="replaybadge">↻ replayed</span>`:"";
  col.appendChild(el(`<div class="runhdr"><span>trajectory <b>#${String(sel.traj+1).padStart(2,"0")}</b></span>`+
    `<span><b>${fmtMs(t.total_ms)}</b></span>`+
    `<span><b>${(t.total_tokens/1000).toFixed(1)}k</b> tok</span>`+
    `<span><b>$${t.total_cost_usd.toFixed(3)}</b></span>${rep}</div>`));
  const rows = el(`<div class="rows"></div>`);
  const total = t.total_ms || 1;
  t.spans.forEach((s,i)=>{
    const sl = i===sel.span?" sel":"";
    const errRow = s.status==="error"?" err":"";
    const left = (s.start_ms/total)*100;
    const width = Math.max((s.duration_ms/total)*100, 1.2);
    const barCls = s.status==="error"?"err":s.kind;
    const pad = 8 + s.depth*16;
    const tail = spanTail(s);
    const row = el(`<div class="row${sl}${errRow}">`+
      `<div class="nm" style="padding-left:${pad}px"><span class="ic ${s.kind}">${ICON[s.kind]||"•"}</span>`+
      `<span>${s.name}</span></div>`+
      `<div class="track"><div class="bar ${barCls}" style="left:${left}%;width:${width}%"></div></div>`+
      `<div class="tail">${tail}</div></div>`);
    row.onclick = ()=>{ sel.span=i; render(); };
    rows.appendChild(row);
  });
  col.appendChild(rows);
  return col;
}

function spanTail(s){
  const a = s.attributes;
  if(s.kind==="model"){
    const tok = (a["gen_ai.usage.input_tokens"]||0)+(a["gen_ai.usage.output_tokens"]||0);
    return `${tok} tok &middot; ${fmtMs(s.duration_ms)}`;
  }
  if(s.kind==="tool"){ return `${a["gen_ai.tool.name"]||""} &middot; ${fmtMs(s.duration_ms)}`; }
  return fmtMs(s.duration_ms);
}

function renderInspector(){
  const t = TRACES[sel.traj];
  const s = t.spans[sel.span];
  const col = el(`<div class="col inspector"></div>`);
  col.appendChild(el(`<div class="hd">inspector &middot; selected span</div>`));
  const ins = el(`<div class="ins"></div>`);
  ins.appendChild(el(`<h3>${s.name}</h3>`));
  ins.appendChild(el(`<div class="sub">${s.kind} &middot; ${fmtMs(s.duration_ms)} @ ${fmtMs(s.start_ms)}</div>`));
  const entries = Object.entries(s.attributes);
  entries.push(["span_id", s.span_id]);
  entries.push(["trace_id", t.trace_id]);
  entries.push(["status", s.status]);
  for(const [k,v] of entries){
    const isErr = (k==="status" && v==="error") || k==="chorus.failure.class" ||
                  (typeof v==="string" && /error|exit 1|fail/i.test(v));
    const val = Array.isArray(v)?v.join(", "):String(v);
    const kv = el(`<div class="kv"><div class="k">${k}</div><div class="v${isErr?" err":""}"></div></div>`);
    kv.querySelector(".v").textContent = val;
    ins.appendChild(kv);
  }
  col.appendChild(ins);
  return col;
}

render();
</script>
</body>
</html>
"""
