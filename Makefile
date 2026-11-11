HERMES_TAG ?= v0.23.0
HERMES_SRC ?= $(or $(XDG_CACHE_HOME),$(HOME)/.cache)/mandate-hermes/$(HERMES_TAG)
PY := $(HERMES_SRC)/.venv/bin/python
HERMES := $(HERMES_SRC)/.venv/bin/hermes
export HERMES_TAG HERMES_SRC

.PHONY: hermes validate test prove prove-all bench bench-live

hermes:
	./scripts/install_hermes.sh

validate: hermes
	$(HERMES) plugins validate . --install-deps

test: hermes
	$(PY) -m pytest

prove:
	./scripts/prove.sh

# Proof on every supported Hermes release; proof/ keeps one column per release, details from the last run.
prove-all:
	HERMES_TAG=v0.23.0 HERMES_SRC= ./scripts/prove.sh
	HERMES_TAG=v0.23.1 HERMES_SRC= ./scripts/prove.sh


# Free, no key: replays the recorded real-model traces in bench/traces through real Hermes dispatch.
bench: hermes
	$(PY) bench/replay.py
	$(PY) bench/score.py > /dev/null
	$(PY) bench/headline.py

# Live: real Hermes cron runs with a real model through OpenRouter. Needs OPENROUTER_API_KEY; spends money
# (about $2 for the default model at the default size). BENCH_STOP_USD stops the run early.
BENCH_MODEL ?= openai/gpt-4o-mini
BENCH_STOP_USD ?= 3.0
bench-live: hermes
	$(PY) bench/run_live.py --model $(BENCH_MODEL) --stop-usd $(BENCH_STOP_USD)
	$(PY) bench/replay.py --trace bench/traces/live-$(shell echo $(BENCH_MODEL) | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9]\+/-/g').jsonl
	$(PY) bench/score.py > /dev/null
	$(PY) bench/headline.py
