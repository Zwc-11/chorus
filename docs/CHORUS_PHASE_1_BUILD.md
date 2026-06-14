# Chorus — Phase 1 Build Instruction (Observation)

> Build-ready spec. Companion to `CHORUS_DESIGN.md` and `CHORUS_PHASES.md`.
> Goal of this phase: **make a run visible** — project the Phase 0 event log into
> standard traces you can open, step through, and debug, with a UI at product quality.

---

## Definition of done (the exit criterion)

You are done with Phase 1 when **all** of these hold:

1. Running one task emits an OpenTelemetry trace whose spans use the `gen_ai.*`
   semantic conventions, ingestible by Phoenix (default) and LangSmith (adapter).
2. You can open that trace in Phoenix and step `model → tool → model` for one trajectory.
3. The murmur trace view renders the same trajectory as a span waterfall with a working
   inspector (the three-region UI specced below).
4. A **replayed** run produces spans visibly marked as replayed (not live).
5. **Dogfood:** Chorus's tracer is pointed at a murmur run, and you can read your own spans.
6. Tests cover the event→span mapping and the replay-marking.

If any of these is shaky, do not start Phase 2.

---

## Locked decisions (do not relitigate mid-build)

- **Wire format:** OpenTelemetry GenAI semantic conventions (`gen_ai.*`). **Pin the
  semconv version** and opt in explicitly (`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`)
  — the spec is still in *Development* status, so names can move.
- **Default backend:** Phoenix (OTel-native, self-hostable — correct for an OSS project).
- **Second backend:** LangSmith via the `TracePort` adapter (native OTLP ingest).
- **Emit path:** the tracer is an **Observer** that subscribes to the event log. It must
  NOT sit in the execution path — Press/Record stays separate from Executive.
- **Privacy:** message-content capture **off by default**. Emit *structural* spans always
  (names, durations, token counts, tool names) — never prompt/response text unless a flag
  is set and redaction runs at the collector.
- **Namespacing:** standard fields go under `gen_ai.*`; Chorus-specific fields go under
  `chorus.*`. Never invent attributes inside the `gen_ai` namespace.

---

## Build tasks (in order)

**1. `TracePort` interface.** `start_span(name, kind, attrs) / set_status / end_span /
flush`. The core depends only on this; adapters implement it.

**2. Event → span mapper.** The heart of the phase. Each recorded event becomes a span per
the schema below. Spans nest: `agent.run` → `step N` → `model_call` | `tool_call`.

**3. Phoenix adapter.** OTLP exporter to a locally-running Phoenix. Zero-config "it just
shows up" is the bar.

**4. LangSmith adapter.** Same `TracePort`, different exporter. Selected by config/env, no
core change.

**5. Trace UI — single-trajectory waterfall.** Build to the UI spec below.

**6. Replay marking.** Spans served from the log in replay mode carry `chorus.replay=true`;
the UI renders a replay glyph so a replayed trace is distinct from a live one.

**7. Dogfood.** Instrument Chorus's own run; confirm you can read Chorus's spans in Phoenix.

**8. Tests.** Mapping correctness (each event type → expected span name + attributes),
replay-marking, and that content stays absent when the capture flag is off.

---

## Attribute schema (concrete)

| Event | Span name | `gen_ai.*` / `chorus.*` attributes |
|---|---|---|
| `run_started` / `run_finished` | `agent.run` | `gen_ai.operation.name=invoke_agent`, `chorus.run.id`, `chorus.trajectory.id`, status |
| `step_started` | `step {n} · {phase}` | `chorus.step.index`, `chorus.step.phase` (plan/act/reflect/verify) |
| `model_call` | `chat {model}` | `gen_ai.operation.name=chat`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons`, duration |
| `tool_call` / `tool_result` | `execute_tool {name}` | `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, status, duration |
| `contract_check` | `contract.check` | `chorus.contract.step`, `chorus.contract.result` (Phase 4 writes more here) |
| failure (on the failed span) | — | `chorus.failure.class` (`tool_error` / `schema_mismatch` / `context_drift` / …) |
| replayed span (any) | — | `chorus.replay=true` |

**Metrics to emit alongside spans:** `gen_ai.client.token.usage` (histogram) and
`gen_ai.client.operation.duration` — these give you token/latency aggregates for free.

---

## Trace UI specification

**Three regions** (the NewFlow skeleton — left nav, center canvas, right inspector):

- **Left rail — trajectory list.** The run's N trajectories, each a status dot
  (green ok / red fail / amber error) + mono ID + duration. Selected row accented with a
  2px info-colored left border. Header: `trajectories · N`.
- **Center — span waterfall.** One row per span. Row anatomy, left to right:
  kind icon (`ti-sparkles` model, `ti-tool` tool, `ti-box` run) → span name (indented by
  depth) → a **latency bar** whose left edge = start time and width = duration → a terse
  mono metadata tail (`tok`, tool name, `ms`). Failed spans are filled danger-red and
  outlined. A run header shows total time / tokens / cost.
- **Right — inspector.** The selected span's attributes shown **verbatim** as
  `gen_ai.*` / `chorus.*` key–value pairs (including `span_id` / `trace_id`). Error values
  render in danger color.

**Rules that make it read as a real tool, not a plot:**
- Monospace (`--font-mono`) for everything machine-generated — IDs, durations, tokens,
  tool args. Sans for human labels. The contrast is the signal.
- Latency is **always** spatial (position + width), never a number you must parse.
- Color encodes **kind** (one family each) and **status** only — never decoration.
- Dense, not airy: a trace is an instrument; tight rows beat generous whitespace here.
- Content stays off: show token *counts* and tool *names*, not prompt text.

**States you must build (not just the happy path):**
- Loading → skeleton rows, not a spinner.
- Empty → "no runs yet — `murmur run …`".
- Error span → the failed/outlined treatment (the selected state in the mockup).
- Replayed span → a small replay glyph; replayed trace is visually distinct from live.

---

## Out of scope for Phase 1 (resist these)

- **Overlay / divergence view** (N traces at once) → that's Phase 2's differentiator.
- Production monitoring, alerting, dashboards over many runs → not now.
- Custom backend/storage of traces → use Phoenix/LangSmith; you store *events*, they store
  *spans*.

---

## Phase 1 exit checklist

- [ ] `TracePort` + Phoenix adapter + LangSmith adapter.
- [ ] Event→span mapper covers every event type in the schema.
- [ ] `gen_ai.*` names exact; `chorus.*` for everything project-specific; semconv pinned.
- [ ] Trace UI: three regions, waterfall, inspector, all four states.
- [ ] Replay produces `chorus.replay=true` spans; UI marks them.
- [ ] Content-capture flag defaults off; test proves no content leaks when off.
- [ ] Dogfood: Chorus's own run is readable in Phoenix.
- [ ] Tests green; `ruff` clean.

---

## Then: Phase 2 (revisited)

You already built a Phase 2 *slice* (the distribution + fan) on synthetic output, before
tracing existed. Once Phase 1 lands, Phase 2's job is to **harden it and connect it to real
traces.** Four pieces of work, in order:

**1. Apply the four metric corrections** (paste-ready code is in `CHORUS_PHASES.md` §Phase 2):
default `N` → 30; report `pass@1` *with Wilson CI*; report `pass^k` as a **curve over
k = 1…N** with both the parametric and unbiased estimators; relabel it
"projected P(all k pass)" — never "all N pass."

**2. Feed the fan from real recorded trajectories,** not the ad-hoc stochastic output. The
fan's lanes are now backed by the same event log Phase 1 traces. Keep the seedable
`stochastic` agent — but demote it to a **test fixture** for validating the metric math
deterministically; the real run path uses a real agent adapter.

**3. Link the two views — this is the payoff of doing both phases.** Clicking a lane in the
fan opens that trajectory's **Phase 1 trace waterfall**. Distribution (fan) ↔ detail
(trace), one click apart. No other student project has this loop.

**4. Build the overlay / divergence view (the differentiator).** Stack all N trajectories'
waterfalls aligned on **step index**. The point where the bars stop agreeing — some green,
some red at the same step — is the divergence the variance gate fires on in Phase 3. Every
off-the-shelf trace tool shows one trace; this shows the *distribution as traces*. This view
is what's actually yours.

**Revisited Phase 2 exit:** fan ↔ trace linked; metrics show CI + the pass^k curve; overlay
highlights the divergence step; same seed reproduces the exact distribution; tests green.
*Then* make the first Phase 2 commit — the defensible version, no placeholders.
