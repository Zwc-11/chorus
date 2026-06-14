# Small command shortcuts for local development.
# These targets wrap the exact Python commands we expect contributors to run.
PYTHON ?= python

.PHONY: install test lint format demo replay run trace gate langsmith

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check murmur tests

format:
	$(PYTHON) -m ruff format murmur tests

demo:
	murmur demo --n 3 --event-log .murmur/demo.jsonl

replay:
	murmur replay --event-log .murmur/demo.jsonl

run:
	murmur run --n 30 --success-rate 0.7 --error-rate 0.1 --seed 7

trace:
	murmur trace --n 30 --seed 7 --replay

gate:
	murmur gate --branch main --n 20 --update-baseline
	murmur gate --branch main --n 20 --scaffold worse --success-delta -0.12 --error-rate 0.12

# Needs the [otel] extra + LANGSMITH_API_KEY. See docs/LANGSMITH_MCP_LOOP.md.
langsmith:
	murmur trace --n 12 --seed 7 --otlp --backend langsmith --project murmur
