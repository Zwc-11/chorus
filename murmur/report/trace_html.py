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

from murmur.report.ui_theme import document_close, document_head, hud_shell_end, hud_shell_start
from murmur.trace.spans import Trace


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
    payload = [_trace_to_json(t) for t in traces]
    data = json.dumps(payload)
    run_label = run_id or (traces[0].run_id if traces else "")
    return _build_template(data, run_label, n_traces=len(payload))


def write_traces_html(traces: list[Trace], path: Path | str, *, run_id: str = "") -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_traces_html(traces, run_id=run_id), encoding="utf-8")
    return out


_TRACE_EXTRA_CSS = r"""
body { overflow: hidden; }
.hud-main { max-width: none; padding: 0; flex: 1; display: flex; flex-direction: column; min-height: 0; }
.trace-app { flex: 1; min-height: 0; }
.grid {
  display: grid;
  grid-template-columns: 220px 1fr 300px;
  height: calc(100vh - 168px);
  min-height: 420px;
}
.col { overflow: auto; height: 100%; }
.rail {
  background: var(--panel);
  border-right: var(--hud-border);
  backdrop-filter: blur(10px);
}
.center { background: transparent; }
.inspector {
  background: var(--panel);
  border-left: var(--hud-border);
  backdrop-filter: blur(10px);
}
.hd {
  padding: 11px 14px;
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
  border-bottom: var(--hud-border);
  position: sticky;
  top: 0;
  background: inherit;
  z-index: 2;
}
.traj {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  cursor: pointer;
  border-left: 2px solid transparent;
}
.traj:hover { background: var(--panel2); }
.traj.sel { background: var(--accent-soft); border-left-color: var(--accent); }
.dot { width: 8px; height: 8px; border-radius: 50%; flex: none; border: 1px solid var(--line-strong); }
.traj .id { font-family: var(--mono); font-size: 12px; }
.traj .dur { margin-left: auto; font-family: var(--mono); color: var(--dim); font-size: 11px; }
.ok { background: var(--txt); }
.fail { background: var(--accent); border-color: var(--accent); box-shadow: 0 0 6px var(--accent-glow); }
.error { background: var(--warn); border-color: var(--warn); }
.runhdr {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  padding: 10px 16px;
  border-bottom: var(--hud-border);
  font-family: var(--mono);
  color: var(--muted);
  font-size: 11px;
  align-items: center;
  background: var(--panel2);
}
.runhdr b { color: var(--txt); font-weight: 500; }
.replaybadge {
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: 2px;
  padding: 1px 6px;
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.rows { padding: 4px 0; }
.row {
  display: grid;
  grid-template-columns: 240px 1fr 140px;
  align-items: center;
  gap: 10px;
  padding: 3px 16px;
  cursor: pointer;
  border: 1px solid transparent;
}
.row:hover { background: var(--panel2); }
.row.sel { background: var(--accent-soft); border-color: var(--line); }
.row.err { background: rgba(232, 25, 42, 0.08); }
.nm {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ic { font-family: var(--mono); width: 14px; text-align: center; flex: none; color: var(--muted); }
.ic.model { color: var(--txt); }
.ic.tool { color: var(--dim); }
.track {
  position: relative;
  height: 12px;
  background: rgba(0, 0, 0, 0.05);
  border: 1px solid var(--line);
  border-radius: 1px;
}
.bar {
  position: absolute;
  top: 0;
  height: 12px;
  border-radius: 1px;
  min-width: 2px;
  opacity: 0.9;
}
.bar.model { background: var(--txt); }
.bar.tool { background: var(--dim); }
.bar.run { background: #8a8a84; }
.bar.step { background: #c4c4be; }
.bar.err {
  background: var(--accent);
  outline: 1px solid var(--accent);
  opacity: 1;
  box-shadow: 0 0 8px var(--accent-glow);
}
.tail {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--dim);
  text-align: right;
  white-space: nowrap;
}
.ins { padding: 14px 16px; }
.ins h3 { margin: 0 0 2px; font-size: 13px; font-weight: 500; }
.ins .sub { color: var(--muted); font-family: var(--mono); font-size: 11px; margin-bottom: 12px; }
.kv { margin: 0 0 10px; }
.kv .k { color: var(--muted); font-size: 10px; font-family: var(--mono); letter-spacing: 0.04em; }
.kv .v { color: var(--txt); font-family: var(--mono); font-size: 12px; word-break: break-word; }
.kv .v.err { color: var(--accent); }
.ins-actions { margin-top: 14px; }
.ins-open {
  border: 1px solid var(--line);
  background: transparent;
  color: var(--txt);
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 8px 12px;
  cursor: pointer;
}
.ins-open:hover { border-color: var(--accent); color: var(--accent); }
.empty {
  display: flex;
  height: calc(100vh - 120px);
  align-items: center;
  justify-content: center;
  color: var(--muted);
  font-family: var(--mono);
  letter-spacing: 0.06em;
}
.hint { color: var(--dim); }
@media (max-width: 900px) {
  .grid { grid-template-columns: 1fr; height: auto; }
  body { overflow: auto; }
}
"""


def _build_template(data_json: str, run_label: str, *, n_traces: int) -> str:
    run_line = (
        f"trace · {run_label} · {n_traces} trajectories"
        if n_traces
        else "trace · no data"
    )
    head = document_head(title=f"murmur trace — {run_label}", extra_css=_TRACE_EXTRA_CSS)
    shell = hud_shell_start(
        brand="chorus",
        run_line=run_line,
        quote="Keep running — latency is spatial, never a number you parse.",
    )
    body = (
        f"{shell}"
        '<div id="app" class="trace-app t-skel" data-chorus-reveal>'
        '<div class="t-skel-skeleton" aria-hidden="true"></div>'
        '<div class="t-skel-content" id="app-inner"></div>'
        "</div>"
        f"{hud_shell_end(footer_left='trace')}"
    )
    script = f"""
const TRACES = {data_json};
const RUN = {json.dumps(run_label)};
const ICON = {{ run:"▢", step:"›", model:"✦", tool:"▤", contract:"✓" }};
let sel = {{ traj:indexFromHash(), span:0 }};

function fmtMs(ms){{ return ms >= 1000 ? (ms/1000).toFixed(2)+"s" : ms.toFixed(0)+"ms"; }}
function el(html){{ const t=document.createElement("template"); t.innerHTML=html.trim(); return t.content.firstChild; }}
function esc(s){{ const d=document.createElement("div"); d.textContent=s; return d.innerHTML; }}

function indexFromHash(){{
  const target = decodeURIComponent(location.hash.replace(/^#/, ""));
  const index = TRACES.findIndex(t => t.trajectory_id === target);
  return index >= 0 ? index : 0;
}}
window.addEventListener("hashchange", () => {{
  const index = indexFromHash();
  if(index !== sel.traj){{ sel={{traj:index, span:0}}; render(); }}
}});

function spanModalHtml(t, s){{
  const entries = Object.entries(s.attributes);
  entries.push(["span_id", s.span_id]);
  entries.push(["trace_id", t.trace_id]);
  entries.push(["status", s.status]);
  let rows = '<p class="lead">' + esc(s.kind) + ' · ' + fmtMs(s.duration_ms) + ' @ ' + fmtMs(s.start_ms) + '</p>';
  for (const [k,v] of entries) {{
    const val = Array.isArray(v) ? v.join(", ") : String(v);
    const err = (k==="status" && v==="error") || k==="chorus.failure.class";
    rows += '<div class="modal-kv"><span class="k">' + esc(k) + '</span><span class="' + (err ? "err" : "") + '">' + esc(val) + '</span></div>';
  }}
  return rows;
}}

function openSpanModal(){{
  const t = TRACES[sel.traj];
  const s = t.spans[sel.span];
  chorusOpenModal("span · " + s.name, spanModalHtml(t, s));
}}

function render(){{
  const mount = document.getElementById("app-inner");
  if (!mount) return;
  mount.innerHTML = "";
  if(!TRACES.length){{
    mount.appendChild(el(`<div class="empty">no runs yet &mdash; <span class="hint">murmur trace &hellip;</span></div>`));
    return;
  }}
  const grid = el(`<div class="grid"></div>`);
  grid.appendChild(renderRail());
  grid.appendChild(renderCenter());
  grid.appendChild(renderInspector());
  mount.appendChild(grid);
}}

function renderRail(){{
  const rail = el(`<div class="col rail"></div>`);
  rail.appendChild(el(`<div class="hd">trajectories · ${{TRACES.length}}</div>`));
  TRACES.forEach((t,i)=>{{
    const cls = t.outcome==="pass"?"ok":(t.outcome==="error"?"error":"fail");
    const sl = i===sel.traj?" sel":"";
    const rep = t.replay?` <span class="ic">↻</span>`:"";
    const row = el(`<div class="traj${{sl}}"><span class="dot ${{cls}}"></span>`+
      `<span class="id">#${{String(i+1).padStart(2,"0")}}${{rep}}</span>`+
      `<span class="dur">${{fmtMs(t.total_ms)}}</span></div>`);
    row.onclick = ()=>{{
      sel={{traj:i, span:0}};
      history.replaceState(null, "", "#"+encodeURIComponent(t.trajectory_id));
      render();
    }};
    rail.appendChild(row);
  }});
  return rail;
}}

function renderCenter(){{
  const t = TRACES[sel.traj];
  const col = el(`<div class="col center"></div>`);
  const rep = t.replay?`<span class="replaybadge">↻ replayed</span>`:"";
  col.appendChild(el(`<div class="runhdr"><span>trajectory <b>#${{String(sel.traj+1).padStart(2,"0")}}</b></span>`+
    `<span><b>${{fmtMs(t.total_ms)}}</b></span>`+
    `<span><b>${{(t.total_tokens/1000).toFixed(1)}}k</b> tok</span>`+
    `<span><b>$${{t.total_cost_usd.toFixed(3)}}</b></span>${{rep}}</div>`));
  const rows = el(`<div class="rows"></div>`);
  const total = t.total_ms || 1;
  t.spans.forEach((s,i)=>{{
    const sl = i===sel.span?" sel":"";
    const errRow = s.status==="error"?" err":"";
    const left = (s.start_ms/total)*100;
    const width = Math.max((s.duration_ms/total)*100, 1.2);
    const barCls = s.status==="error"?"err":s.kind;
    const pad = 8 + s.depth*16;
    const tail = spanTail(s);
    const row = el(`<div class="row${{sl}}${{errRow}}">`+
      `<div class="nm" style="padding-left:${{pad}}px"><span class="ic ${{s.kind}}">${{ICON[s.kind]||"•"}}</span>`+
      `<span>${{s.name}}</span></div>`+
      `<div class="track"><div class="bar ${{barCls}}" style="left:${{left}}%;width:${{width}}%"></div></div>`+
      `<div class="tail">${{tail}}</div></div>`);
    row.onclick = ()=>{{ sel.span=i; render(); }};
    row.ondblclick = (e)=>{{ e.preventDefault(); sel.span=i; openSpanModal(); }};
    rows.appendChild(row);
  }});
  col.appendChild(rows);
  return col;
}}

function spanTail(s){{
  const a = s.attributes;
  if(s.kind==="model"){{
    const tok = (a["gen_ai.usage.input_tokens"]||0)+(a["gen_ai.usage.output_tokens"]||0);
    return `${{tok}} tok · ${{fmtMs(s.duration_ms)}}`;
  }}
  if(s.kind==="tool"){{ return `${{a["gen_ai.tool.name"]||""}} · ${{fmtMs(s.duration_ms)}}`; }}
  return fmtMs(s.duration_ms);
}}

function renderInspector(){{
  const t = TRACES[sel.traj];
  const s = t.spans[sel.span];
  const col = el(`<div class="col inspector"></div>`);
  col.appendChild(el(`<div class="hd">inspector · selected span</div>`));
  const ins = el(`<div class="ins"></div>`);
  ins.appendChild(el(`<h3>${{s.name}}</h3>`));
  ins.appendChild(el(`<div class="sub">${{s.kind}} · ${{fmtMs(s.duration_ms)}} @ ${{fmtMs(s.start_ms)}}</div>`));
  const entries = Object.entries(s.attributes);
  entries.push(["span_id", s.span_id]);
  entries.push(["trace_id", t.trace_id]);
  entries.push(["status", s.status]);
  for(const [k,v] of entries){{
    const isErr = (k==="status" && v==="error") || k==="chorus.failure.class" ||
                  (typeof v==="string" && /error|exit 1|fail/i.test(v));
    const val = Array.isArray(v)?v.join(", "):String(v);
    const kv = el(`<div class="kv"><div class="k">${{k}}</div><div class="v${{isErr?" err":""}}"></div></div>`);
    kv.querySelector(".v").textContent = val;
    ins.appendChild(kv);
  }}
  const actions = el(`<div class="ins-actions"><button type="button" class="ins-open">expand in modal</button></div>`);
  actions.querySelector("button").onclick = openSpanModal;
  ins.appendChild(actions);
  col.appendChild(ins);
  return col;
}}

render();
"""
    return head + body + document_close(extra_script=script)
