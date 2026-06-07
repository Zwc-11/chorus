# The LangSmith → MCP self-debug loop

> Phase 6 deliverable (architecture.md §6). Murmur emits a run as an OpenTelemetry
> trace, the trace lands in LangSmith, and a coding agent pulls it back through the
> **LangSmith MCP server** to debug a real Murmur bug — the "write → trace → debug"
> loop closed on Murmur itself (dogfooding).

```
murmur run/trace ──emit gen_ai.* spans──▶ LangSmith project
        ▲                                        │
        │ fix the bug                            │ pull the trace
        │                                        ▼
   Murmur source ◀──debug from the trace── coding agent + LangSmith MCP server
```

The export adapter and the MCP config are in the repo; the **live** hops (a real
LangSmith account, a real run, a real bug) are yours to run — nothing here fakes a
trace or a finding.

---

## 0. Prerequisites (one-time)

```bash
pip install -e ".[otel]"           # the OpenTelemetry exporter
export LANGSMITH_API_KEY=ls-...    # from smith.langchain.com → Settings → API Keys
export LANGSMITH_PROJECT=murmur    # any project name; created on first export
```

The MCP server runs via `uvx` (from [uv](https://github.com/astral-sh/uv)); no
separate install — `uvx langsmith-mcp-server` fetches it on first use.

---

## 1. Emit a Murmur run to LangSmith

```bash
murmur trace --n 12 --seed 7 --otlp --backend langsmith --project murmur
```

This records a run, projects its events into `gen_ai.*` spans (model / tool / step
nested under `agent.run`), and exports them over OTLP to
`https://api.smith.langchain.com/otel/v1/traces` with your `x-api-key` and the
`Langsmith-Project` header. The command prints the project URL to open. Content
capture stays **off** by default — only structural spans (names, token counts, tool
names, durations, `murmur.*` attributes) leave the machine; add `--capture-content`
only if you want prompt/arg text in LangSmith.

> Tip: trace a run that actually failed (`--error-rate 0.3`, or a real agent via the
> integrated path) so there is a real defect in the trace to debug.

## 2. Connect the LangSmith MCP server

The repo ships [`.mcp.json`](../.mcp.json):

```json
{
  "mcpServers": {
    "langsmith": {
      "command": "uvx",
      "args": ["langsmith-mcp-server"],
      "env": { "LANGSMITH_API_KEY": "${LANGSMITH_API_KEY}" }
    }
  }
}
```

Open this repo in a coding agent that reads `.mcp.json` (e.g. Claude Code) with
`LANGSMITH_API_KEY` exported. The server exposes tools to **list projects** and
**fetch runs/traces** (plus prompts, datasets, experiments). The key is read from the
environment via `${LANGSMITH_API_KEY}` — no secret is committed.

## 3. Pull the trace and debug Murmur from it

Drive the loop from inside the coding agent, for example:

> "Use the LangSmith MCP server to fetch the most recent run in project `murmur`.
> Walk the `agent.run → step → model/tool` spans, find the step where `murmur.replay`
> diverges or `murmur.failure.class` is set, and trace it back to the Murmur source
> that produced that span."

The exporter translates Murmur attributes into LangSmith's OTEL conventions, so in
LangSmith each run carries `metadata.murmur.run.id`, `metadata.murmur.trajectory.id`,
`metadata.murmur.step.phase`, and (on failures) `metadata.murmur.failure.class` /
`metadata.murmur.failure.step` — and **failed/errored trajectories are marked as
error runs** (via an OTel exception event). So the agent can filter the project to
error runs, read the failure class and step from metadata, and map a LangSmith span
straight to the event in `.murmur/trace.jsonl` and the code path that emitted it —
then propose the fix. (Verified live: a 20-trajectory export landed 11 error runs,
each tagged e.g. `murmur.failure.class=contract_violation`, `failure.step=5`.)

## 4. Close the loop

Apply the fix, re-run step 1, and confirm the divergence/failure span is gone in the
new LangSmith trace. That round trip — Murmur's own bug, found and fixed *through its
own trace* — is the dogfood demo.

---

## What is Tier A vs Tier B here

- **Tier A (in the repo, tested):** the OTLP→LangSmith exporter
  ([murmur/adapters/trace/otlp.py](../murmur/adapters/trace/otlp.py)), the
  `murmur trace --otlp --backend langsmith --project` ergonomics, the `.mcp.json`,
  and this runbook. The export pipeline is verified backend-agnostically with an
  in-memory port in [tests/test_phase6_langsmith.py](../tests/test_phase6_langsmith.py).
- **Tier B (you run it):** the live export to a real LangSmith project and the actual
  MCP-driven debugging of a real Murmur bug — needs `LANGSMITH_API_KEY`, the `[otel]`
  extra, and `uvx`. Documented, never faked.
