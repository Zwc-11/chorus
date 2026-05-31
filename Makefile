PYTHON ?= python

.PHONY: install test lint format demo replay

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

