# Run Chorus On Your Agent In 10 Minutes

Chorus wraps an agent. It does not replace your runtime. The harness runs the
same task many times, records neutral events, judges outcomes independently, and
reports reliability as a distribution.

## 1. Install

```bash
python -m pip install -e ".[dev]"
chorus init
pytest -q
ruff check chorus tests
```

## 2. Run The Free Demo

```bash
chorus agents list
chorus run --n 30 --seed 7
chorus trace --n 12 --seed 7 --replay
chorus gate --suite synthetic --n 20 --k 5
```

Open `.chorus/fan.html` or `.chorus/trace.html` after the run. These reports are
derived from the event log, not from model self-report.

## 3. Wrap A Real Agent

Implement `AgentPort`:

```python
class MyAgent:
    async def run(self, task, gateway):
        await gateway.step(index=0, phase="plan")
        await gateway.model(
            model="my-model",
            input_tokens=100,
            output_tokens=50,
            finish_reason="stop",
        )
        result = await gateway.call("my_tool", {"input": task.prompt})
        return str(result)
```

For observational frameworks, import traces instead of executing through Chorus:

```python
from chorus.adapters.trace import OpenAIAgentsTraceImporter

events = OpenAIAgentsTraceImporter().import_events(
    records,
    run_id="run_external",
    task_id="my.task",
)
```

Supported public adapter surfaces:

- OpenAI Agents SDK trace import.
- Claude Code hook/transcript import.
- Google ADK trace import.
- LangGraph `astream_events` live adapter and trace import.

## 4. Run A Public Benchmark

Use public benchmarks only for real measured claims. SWE-bench Verified is useful
for calibration, but current frontier claims should prefer fresher or harder
sets such as SWE-bench Pro, SWE-rebench, or Terminal-Bench when available.

```bash
python -m pip install -e ".[bench]"
export ANTHROPIC_API_KEY=...
chorus bench --subset 50 --n 10 --k 5 \
  --scaffold-a single-shot \
  --scaffold-b self-repair
```

If Docker, a dataset, or a real model key is missing, Chorus exits instead of
printing a fake benchmark number.
