"""Interactive Murmur workflow tree — Three.js growing agent forest."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from chorus.domain.workflow import WorkflowPlan
from chorus.report.ui_theme import (
    chorus_modal_markup,
    chorus_ui_js,
    document_head,
    hud_shell_end,
    hud_shell_start,
)

_THREE_IMPORTMAP = """
<script type="importmap">
{
  "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"
  }
}
</script>
"""

_RESUME_DEMO = {
    "goal": "rank 80 resumes for a backend role; verify the top 10",
    "budget": {"budget_tokens": 200000},
    "nodes": [
        {
            "id": "score",
            "op": "map",
            "inputs": ["resumes"],
            "params": {"fan": 80},
            "role": "Score resume against backend rubric",
            "model": "deepseek-v4-flash",
        },
        {
            "id": "shortlist",
            "op": "filter",
            "inputs": ["score"],
            "params": {"top_k": 20},
        },
        {
            "id": "bracket",
            "op": "tournament",
            "inputs": ["shortlist"],
            "role": "Pick stronger backend candidate",
            "model": "deepseek-v4-flash",
        },
        {
            "id": "check",
            "op": "verify",
            "inputs": ["bracket"],
            "params": {"top_k": 10},
            "role": "Adversarially challenge ranking",
            "model": "deepseek-v4-pro",
        },
        {
            "id": "report",
            "op": "reduce",
            "inputs": ["check"],
            "role": "Synthesize final ranked list",
            "model": "deepseek-v4-pro",
        },
    ],
}

_FIX_TEST_SHAPE = {
    "goal": "Closed-loop fix-test: reproduce → generate → verify → report",
    "budget": {"max_candidates": 4},
    "nodes": [
        {"id": "reproduce", "op": "exec", "inputs": []},
        {"id": "generate", "op": "generate", "inputs": ["reproduce"], "params": {"n": 4}},
        {"id": "run_tests", "op": "exec", "inputs": ["generate"]},
        {"id": "repair", "op": "loop", "inputs": ["run_tests"]},
        {"id": "rank", "op": "rank", "inputs": ["repair"]},
        {"id": "verify", "op": "verify", "inputs": ["rank"]},
        {"id": "report", "op": "report", "inputs": ["verify"]},
    ],
}


def render_murmur_workflow_html(
    *,
    workflow: WorkflowPlan | None = None,
    embedded_task: str = "",
) -> str:
    payload = workflow.to_dict() if workflow else None
    embedded_json = json.dumps(payload) if payload else "null"
    task_default = embedded_task or (
        workflow.goal if workflow else "Rank these resumes for a backend role and verify the top 10."
    )
    demos_json = json.dumps({"resume": _RESUME_DEMO, "fix_test": _FIX_TEST_SHAPE})
    module_js = _MURMUR_MODULE_JS.replace("__EMBEDDED__", embedded_json).replace(
        "__DEMOS__", demos_json
    )

    head = document_head(title="Murmur — workflow tree", extra_css=_MURMUR_CSS)
    shell = hud_shell_start(
        brand="murmur",
        run_line="planner → typed IR → parallel operators · three.js forest",
        quote="Thousands of cheap birds flocking into one intelligent shape.",
    )
    body = f"""
<section class="hud-widget murmur-controls">
  <div class="hud-widget__hd">task &amp; fan-out</div>
  <div class="hud-widget__bd">
    <label class="murmur-label" for="murmur-task">Natural-language task</label>
    <textarea id="murmur-task" class="murmur-task" rows="3">{escape(task_default)}</textarea>
    <div class="murmur-row">
      <label class="murmur-label" for="murmur-agents">Parallel agents (map / generate)</label>
      <input id="murmur-agents" class="murmur-agents" type="range" min="1" max="16" value="4"/>
      <output id="murmur-agents-out" class="murmur-agents-out">4</output>
    </div>
    <div class="murmur-presets">
      <button type="button" class="murmur-btn" data-preset="resume">Resume ranking demo</button>
      <button type="button" class="murmur-btn" data-preset="fix_test">Fix-test closed loop</button>
      <button type="button" class="murmur-btn murmur-btn--accent" id="murmur-plan">Plan workflow</button>
      <button type="button" class="murmur-btn" id="murmur-run" disabled>Execute plan</button>
      <button type="button" class="murmur-btn murmur-btn--ghost" id="murmur-reset">Reset tree</button>
    </div>
    <p class="murmur-hint">Drag to orbit · scroll to zoom · click a sphere for details. Branches grow from each agent; parallel agents each sprout their own subtree.</p>
  </div>
</section>

<section class="hud-widget murmur-stage-wrap">
  <div class="hud-widget__hd">workflow forest · <span id="murmur-status">idle</span></div>
  <div class="hud-widget__bd murmur-stage">
    <canvas id="murmur-canvas" class="murmur-canvas" aria-label="Murmur 3D workflow tree"></canvas>
    <div class="murmur-hud-tip">three.js · growing branches</div>
  </div>
</section>

<section class="hud-widget">
  <div class="hud-widget__hd">operators</div>
  <div class="hud-widget__bd murmur-ops">
    <span class="murmur-op" data-op="classify">classify</span>
    <span class="murmur-op" data-op="map">map</span>
    <span class="murmur-op" data-op="reduce">reduce</span>
    <span class="murmur-op" data-op="tournament">tournament</span>
    <span class="murmur-op" data-op="verify">verify</span>
    <span class="murmur-op" data-op="filter">filter</span>
    <span class="murmur-op" data-op="loop">loop</span>
    <span class="murmur-op" data-op="generate">generate</span>
    <span class="murmur-op" data-op="exec">exec</span>
  </div>
</section>
"""
    close = f"""{chorus_modal_markup()}
<script>
{chorus_ui_js()}
</script>
<script type="module">
{module_js}
</script>
</body>
</html>"""
    return head + _THREE_IMPORTMAP + "<body>" + shell + body + hud_shell_end() + close


def write_murmur_workflow_html(
    path: Path | str,
    *,
    workflow: WorkflowPlan | None = None,
    embedded_task: str = "",
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_murmur_workflow_html(workflow=workflow, embedded_task=embedded_task),
        encoding="utf-8",
    )
    return out


_MURMUR_CSS = r"""
.murmur-controls .murmur-label {
  display: block;
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 6px;
}
.murmur-task {
  width: 100%;
  font-family: var(--sans);
  font-size: 14px;
  line-height: 1.45;
  padding: 12px 14px;
  border: var(--hud-border);
  background: var(--panel-solid);
  color: var(--txt);
  resize: vertical;
  min-height: 72px;
}
.murmur-row {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 16px 0;
  flex-wrap: wrap;
}
.murmur-agents { flex: 1; min-width: 120px; accent-color: var(--accent); }
.murmur-agents-out {
  font: 500 24px/1 var(--mono);
  min-width: 2ch;
  color: var(--accent);
}
.murmur-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
}
.murmur-btn {
  border: 1px solid var(--line);
  background: var(--panel-solid);
  color: var(--txt);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 10px 14px;
  cursor: pointer;
  font-family: var(--mono);
}
.murmur-btn:hover { border-color: var(--accent); color: var(--accent); }
.murmur-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.murmur-btn--accent {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.murmur-btn--ghost { background: transparent; }
.murmur-hint {
  margin: 14px 0 0;
  font-size: 12px;
  color: var(--dim);
  line-height: 1.5;
  max-width: 62ch;
}
.murmur-stage-wrap .hud-widget__bd { padding: 0; position: relative; }
.murmur-stage {
  position: relative;
  min-height: 480px;
  height: 52vh;
  overflow: hidden;
  background: radial-gradient(ellipse 80% 60% at 50% 100%, rgba(232,25,42,0.06), transparent 55%),
              linear-gradient(180deg, rgba(255,255,255,0.4) 0%, rgba(228,228,224,0.9) 100%);
}
.murmur-canvas {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 480px;
  cursor: grab;
}
.murmur-canvas:active { cursor: grabbing; }
.murmur-hud-tip {
  pointer-events: none;
  position: absolute;
  right: 14px;
  bottom: 10px;
  font: 10px var(--mono);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--dim);
}
.murmur-ops {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.murmur-op {
  font: 11px var(--mono);
  padding: 6px 10px;
  border: 1px solid var(--line);
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.murmur-op.is-lit {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}
"""

_MURMUR_MODULE_JS = r"""
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const EMBEDDED = __EMBEDDED__;
const DEMOS = __DEMOS__;

const canvas = document.getElementById("murmur-canvas");
const statusEl = document.getElementById("murmur-status");
const taskEl = document.getElementById("murmur-task");
const agentsEl = document.getElementById("murmur-agents");
const agentsOut = document.getElementById("murmur-agents-out");
const planBtn = document.getElementById("murmur-plan");
const runBtn = document.getElementById("murmur-run");
const resetBtn = document.getElementById("murmur-reset");

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const growDur = () => (reducedMotion ? 0 : 0.55);
const pauseMs = () => (reducedMotion ? 0 : 520);

const COL = {
  accent: 0xe8192a,
  node: 0x2d2d2a,
  planner: 0x1a1a1a,
  agent: 0x6a6a66,
  leaf: 0x9a9a94,
  branch: 0x5a5a56,
  branchAccent: 0xe8192a,
  glow: 0xe8192a,
};

let plan = null;
let running = false;
let forest = null;

function agentCount() {
  return Math.max(1, parseInt(agentsEl.value, 10) || 1);
}

agentsEl.addEventListener("input", () => {
  agentsOut.textContent = agentsEl.value;
});

function setStatus(text) {
  statusEl.textContent = text;
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function fanOps() {
  return new Set(["map", "generate"]);
}

function topoLayers(nodes) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const depth = new Map();
  function d(id) {
    if (depth.has(id)) return depth.get(id);
    const node = byId.get(id);
    if (!node || !node.inputs || !node.inputs.length) {
      depth.set(id, 0);
      return 0;
    }
    const val = 1 + Math.max(...node.inputs.map((inp) => d(inp)));
    depth.set(id, val);
    return val;
  }
  nodes.forEach((n) => d(n.id));
  const maxD = Math.max(0, ...depth.values());
  const layers = [];
  for (let i = 0; i <= maxD; i++) layers.push([]);
  nodes.forEach((n) => layers[depth.get(n.id)].push(n));
  return layers;
}

function goldenDirs(count, radius) {
  const dirs = [];
  const phi = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const y = count === 1 ? 0.5 : 1 - (i / Math.max(1, count - 1)) * 0.85;
    const r = Math.sqrt(Math.max(0.05, 1 - y * y)) * radius;
    const theta = phi * i;
    dirs.push(
      new THREE.Vector3(Math.cos(theta) * r, y * radius + 1.1, Math.sin(theta) * r)
    );
  }
  return dirs;
}

class MurmurForest {
  constructor(canvasEl) {
    this.clock = new THREE.Clock();
    this.nodes = new Map();
    this.branches = [];
    this.growing = [];
    this._raycaster = new THREE.Raycaster();
    this._pointer = new THREE.Vector2();
    this._clickables = [];

    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0xe4e4e0, 0.045);

    this.camera = new THREE.PerspectiveCamera(48, 1, 0.1, 200);
    this.camera.position.set(6, 5, 11);

    this.renderer = new THREE.WebGLRenderer({
      canvas: canvasEl,
      antialias: true,
      alpha: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0xe4e4e0, 0);

    const amb = new THREE.AmbientLight(0xffffff, 0.72);
    const key = new THREE.DirectionalLight(0xffffff, 0.9);
    key.position.set(4, 10, 6);
    const rim = new THREE.DirectionalLight(0xe8192a, 0.35);
    rim.position.set(-6, 2, -4);
    this.scene.add(amb, key, rim);

    const grid = new THREE.GridHelper(24, 24, 0xcacac4, 0xd8d8d2);
    grid.position.y = -0.02;
    grid.material.opacity = 0.35;
    grid.material.transparent = true;
    this.scene.add(grid);

    this.controls = new OrbitControls(this.camera, canvasEl);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.06;
    this.controls.target.set(0, 2.5, 0);
    this.controls.maxPolarAngle = Math.PI * 0.48;
    this.controls.minDistance = 4;
    this.controls.maxDistance = 28;

    canvasEl.addEventListener("pointerdown", (e) => this._onClick(e));
    window.addEventListener("resize", () => this._resize());
    this._resize();
    this._animate();
  }

  _resize() {
    const parent = canvas.parentElement;
    const w = parent.clientWidth;
    const h = Math.max(480, parent.clientHeight || 480);
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _sphere(kind) {
    const scale = kind === "planner" ? 0.38 : kind === "agent" ? 0.2 : kind === "leaf" ? 0.12 : 0.28;
    const geo = new THREE.SphereGeometry(scale, 24, 24);
    const mat = new THREE.MeshStandardMaterial({
      color: kind === "planner" ? COL.planner : kind === "agent" ? COL.agent : kind === "leaf" ? COL.leaf : COL.node,
      roughness: 0.45,
      metalness: 0.08,
      emissive: kind === "planner" ? COL.glow : 0x000000,
      emissiveIntensity: kind === "planner" ? 0.22 : 0,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.scale.setScalar(reducedMotion ? 1 : 0.001);
    return mesh;
  }

  _growBranch(from, to, accent) {
    const dir = new THREE.Vector3().subVectors(to, from);
    const len = dir.length();
    if (len < 0.01) return null;
    const geo = new THREE.CylinderGeometry(accent ? 0.04 : 0.03, accent ? 0.055 : 0.04, len, 8);
    const mat = new THREE.MeshStandardMaterial({
      color: accent ? COL.branchAccent : COL.branch,
      roughness: 0.55,
      emissive: accent ? COL.glow : 0x000000,
      emissiveIntensity: accent ? 0.15 : 0,
    });
    const mesh = new THREE.Mesh(geo, mat);
    const mid = new THREE.Vector3().addVectors(from, to).multiplyScalar(0.5);
    mesh.position.copy(mid);
    mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().normalize());
    mesh.scale.set(1, reducedMotion ? 1 : 0.001, 1);
    this.scene.add(mesh);
    const entry = { mesh, progress: reducedMotion ? 1 : 0, dur: growDur() };
    this.branches.push(entry);
    this.growing.push(entry);
    return mesh;
  }

  _popNode(mesh) {
    if (reducedMotion) {
      mesh.scale.setScalar(1);
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      const entry = { mesh, progress: 0, dur: growDur(), kind: "node" };
      this.growing.push(entry);
      setTimeout(resolve, pauseMs());
    });
  }

  addNode(id, label, worldPos, meta, kind = "op") {
    const mesh = this._sphere(kind);
    mesh.position.copy(worldPos);
    mesh.userData = { id, label, meta: meta || {}, kind };
    this.scene.add(mesh);
    this.nodes.set(id, { mesh, pos: worldPos.clone(), meta, kind });
    this._clickables.push(mesh);
    return this._popNode(mesh);
  }

  connect(fromId, toId, accent = false) {
    const a = this.nodes.get(fromId);
    const b = this.nodes.get(toId);
    if (!a || !b) return;
    this._growBranch(a.pos, b.pos, accent);
  }

  setActive(id) {
    this.nodes.forEach((entry, key) => {
      const active = key === id;
      const mat = entry.mesh.material;
      if (active) {
        mat.emissive.setHex(COL.glow);
        mat.emissiveIntensity = 0.45;
      } else if (entry.kind === "planner") {
        mat.emissive.setHex(COL.glow);
        mat.emissiveIntensity = 0.22;
      } else {
        mat.emissive.setHex(0x000000);
        mat.emissiveIntensity = 0;
      }
    });
    const op = (this.nodes.get(id)?.meta?.op || "").toLowerCase();
    document.querySelectorAll(".murmur-op").forEach((chip) => {
      chip.classList.toggle("is-lit", chip.dataset.op === op);
    });
  }

  clearAgentSpawns() {
    const drop = [...this.nodes.keys()].filter((k) => k.includes(":agent:") || k.includes(":leaf:"));
    drop.forEach((id) => {
      const entry = this.nodes.get(id);
      if (!entry) return;
      this.scene.remove(entry.mesh);
      entry.mesh.geometry.dispose();
      entry.mesh.material.dispose();
      this._clickables = this._clickables.filter((m) => m !== entry.mesh);
      this.nodes.delete(id);
    });
    this.branches = this.branches.filter((b) => {
      const dead = b.mesh.userData?.spawn;
      if (dead) {
        this.scene.remove(b.mesh);
        b.mesh.geometry.dispose();
        b.mesh.material.dispose();
        return false;
      }
      return true;
    });
  }

  async spawnSubtree(parentId, count, accent) {
    const parent = this.nodes.get(parentId);
    if (!parent) return [];
    const radius = 1.4 + Math.min(count, 8) * 0.12;
    const dirs = goldenDirs(count, radius);
    const ids = [];
    for (let i = 0; i < count; i++) {
      const id = parentId + ":agent:" + i;
      const pos = parent.pos.clone().add(dirs[i]);
      await this.addNode(id, "agent " + (i + 1), pos, {
        op: "subagent",
        parent: parentId,
        index: i + 1,
        role: "Isolated context · focused subtask",
      }, "agent");
      const branch = this._growBranch(parent.pos, pos, accent);
      if (branch) branch.userData.spawn = true;
      ids.push(id);

      const leafId = id + ":leaf";
      const leafPos = pos.clone().add(new THREE.Vector3(0, 0.85, 0.35));
      await this.addNode(leafId, "task", leafPos, {
        op: "work",
        parent: id,
        role: "Subtask spawned by agent",
      }, "leaf");
      const leafBranch = this._growBranch(pos, leafPos, accent);
      if (leafBranch) leafBranch.userData.spawn = true;
    }
    return ids;
  }

  clear() {
    this.nodes.forEach((entry) => {
      this.scene.remove(entry.mesh);
      entry.mesh.geometry.dispose();
      entry.mesh.material.dispose();
    });
    this.branches.forEach((b) => {
      this.scene.remove(b.mesh);
      b.mesh.geometry.dispose();
      b.mesh.material.dispose();
    });
    this.nodes.clear();
    this.branches = [];
    this.growing = [];
    this._clickables = [];
    this.controls.target.set(0, 2.5, 0);
  }

  _onClick(event) {
    const rect = canvas.getBoundingClientRect();
    this._pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this._pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this._raycaster.setFromCamera(this._pointer, this.camera);
    const hits = this._raycaster.intersectObjects(this._clickables, false);
    if (!hits.length) return;
    const { id, meta } = hits[0].object.userData;
    openNodeModal(id, meta);
  }

  _animate() {
    requestAnimationFrame(() => this._animate());
    const dt = this.clock.getDelta();
    this.growing = this.growing.filter((g) => {
      g.progress = Math.min(1, g.progress + dt / g.dur);
      const eased = 1 - Math.pow(1 - g.progress, 3);
      if (g.kind === "node") {
        g.mesh.scale.setScalar(eased);
      } else {
        g.mesh.scale.y = eased;
      }
      return g.progress < 1;
    });
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}

function openNodeModal(id, meta) {
  let html = '<p class="lead">' + esc(id) + "</p>";
  ["op", "role", "model", "goal", "parent", "index"].forEach((k) => {
    if (meta[k] != null && meta[k] !== "") {
      html += '<div class="modal-kv"><span class="k">' + esc(k) + '</span><span>' + esc(String(meta[k])) + "</span></div>";
    }
  });
  if (meta.params) {
    html += '<div class="modal-kv"><span class="k">params</span><span>' + esc(JSON.stringify(meta.params)) + "</span></div>";
  }
  if (meta.inputs && meta.inputs.length) {
    html += '<div class="modal-kv"><span class="k">inputs</span><span>' + esc(meta.inputs.join(", ")) + "</span></div>";
  }
  if (typeof chorusOpenModal === "function") chorusOpenModal("node · " + id, html);
}

function ensureForest() {
  if (!forest) forest = new MurmurForest(canvas);
  return forest;
}

async function showPlanner() {
  const f = ensureForest();
  f.clear();
  setStatus("planner thinking");
  await f.addNode("task", "task", new THREE.Vector3(0, 0.2, 0), { op: "input", goal: taskEl.value.trim() }, "op");
  await f.addNode("planner", "planner", new THREE.Vector3(0, 1.4, 0), {
    op: "planner",
    role: "Compile task → Workflow IR",
    model: "deepseek-v4-pro",
  }, "planner");
  f.connect("task", "planner", false);
}

async function layoutPlan(p) {
  plan = p;
  runBtn.disabled = false;
  const f = ensureForest();
  const layers = topoLayers(p.nodes || []);
  const external = new Set();
  (p.nodes || []).forEach((n) => {
    (n.inputs || []).forEach((inp) => {
      if (!(p.nodes || []).some((x) => x.id === inp)) external.add(inp);
    });
  });

  let z = -3;
  for (const ext of external) {
    await f.addNode("src:" + ext, ext, new THREE.Vector3(0, 0.5, z), { op: "source", id: ext }, "op");
    f.connect("planner", "src:" + ext, false);
    z -= 2.2;
  }

  const yStep = 2.4;
  const xSpread = 4.5;
  const positions = new Map();
  for (let li = 0; li < layers.length; li++) {
    const row = layers[li];
    const y = 2.8 + li * yStep;
    row.forEach((node, idx) => {
      const t = row.length === 1 ? 0.5 : idx / Math.max(1, row.length - 1);
      const x = (t - 0.5) * xSpread * Math.max(1, row.length * 0.65);
      const zOff = (li % 2 === 0 ? 1 : -1) * (idx % 2) * 1.2;
      positions.set(node.id, new THREE.Vector3(x, y, zOff));
    });
  }
  for (const node of layers.flat()) {
    await f.addNode(node.id, node.id, positions.get(node.id), Object.assign({}, node), "op");
  }

  for (const node of p.nodes || []) {
    for (const inp of node.inputs || []) {
      const from = (p.nodes || []).some((x) => x.id === inp) ? inp : "src:" + inp;
      if (f.nodes.has(from) && f.nodes.has(node.id)) f.connect(from, node.id, false);
    }
  }

  for (const node of p.nodes || []) {
    await f._popNode(f.nodes.get(node.id).mesh);
    await new Promise((r) => setTimeout(r, reducedMotion ? 0 : 90));
  }
  setStatus("planned · " + (p.nodes || []).length + " nodes");
}

async function executePlan() {
  if (!plan || running) return;
  running = true;
  runBtn.disabled = true;
  const f = ensureForest();
  f.clearAgentSpawns();
  const agents = agentCount();
  const ordered = topoLayers(plan.nodes || []).flat();

  for (const node of ordered) {
    f.setActive(node.id);
    setStatus("running · " + node.op + " · " + node.id);
    await f._popNode(f.nodes.get(node.id).mesh);

    let count = 0;
    if (fanOps().has(node.op)) {
      const n = node.params?.n || node.params?.fan || agents;
      count = Math.min(16, Math.max(1, parseInt(n, 10) || agents));
    } else if (node.op === "tournament") {
      count = 2;
    } else if (node.op === "verify") {
      count = Math.min(agents, node.params?.top_k || 3);
    }

    if (count > 0) {
      await f.spawnSubtree(node.id, count, true);
      await new Promise((r) => setTimeout(r, pauseMs()));
    } else {
      await new Promise((r) => setTimeout(r, pauseMs() * 0.6));
    }
  }

  f.setActive("");
  setStatus("complete");
  running = false;
  runBtn.disabled = false;
}

function buildPlanFromTask() {
  const task = taskEl.value.trim();
  if (EMBEDDED) return Object.assign({}, EMBEDDED, { goal: task || EMBEDDED.goal });
  return {
    goal: task,
    budget: { budget_tokens: 100000 },
    nodes: [
      { id: "classify", op: "classify", inputs: [], role: "Route task branches" },
      {
        id: "work",
        op: "map",
        inputs: ["classify"],
        params: { n: agentCount() },
        role: "Parallel isolated subagents",
      },
      { id: "merge", op: "reduce", inputs: ["work"], role: "Synthesize outputs" },
    ],
  };
}

planBtn.addEventListener("click", async () => {
  planBtn.disabled = true;
  await showPlanner();
  await layoutPlan(buildPlanFromTask());
  planBtn.disabled = false;
});

runBtn.addEventListener("click", () => executePlan());
resetBtn.addEventListener("click", () => {
  if (forest) forest.clear();
  plan = null;
  runBtn.disabled = true;
  setStatus("idle");
  document.querySelectorAll(".murmur-op").forEach((el) => el.classList.remove("is-lit"));
});

document.querySelectorAll("[data-preset]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const demo = DEMOS[btn.dataset.preset];
    if (!demo) return;
    taskEl.value = demo.goal || "";
    if (demo.budget?.max_candidates) agentsEl.value = String(demo.budget.max_candidates);
    const gen = (demo.nodes || []).find((n) => n.op === "generate");
    if (gen?.params?.n) agentsEl.value = String(gen.params.n);
    agentsOut.textContent = agentsEl.value;
    planBtn.click();
  });
});

if (EMBEDDED) setTimeout(() => planBtn.click(), 400);
"""
