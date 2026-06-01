# Small command shortcuts for local development.
# These targets wrap the exact Python commands we expect contributors to run.
PYTHON ?= python

.PHONY: install test lint format demo replay run trace

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check chorus tests

format:
	$(PYTHON) -m ruff format chorus tests

demo:
	chorus demo --n 3 --event-log .chorus/demo.jsonl

replay:
	chorus replay --event-log .chorus/demo.jsonl

run:
	chorus run --n 30 --success-rate 0.7 --error-rate 0.1 --seed 7

trace:
	chorus trace --n 30 --seed 7 --replay
