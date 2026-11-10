HERMES_TAG ?= v0.23.0
HERMES_SRC ?= $(or $(XDG_CACHE_HOME),$(HOME)/.cache)/mandate-hermes/$(HERMES_TAG)
PY := $(HERMES_SRC)/.venv/bin/python
HERMES := $(HERMES_SRC)/.venv/bin/hermes
export HERMES_TAG HERMES_SRC

.PHONY: hermes validate test prove prove-all

hermes:
	./scripts/install_hermes.sh

validate: hermes
	$(HERMES) plugins validate . --install-deps

test: hermes
	$(PY) -m pytest

prove:
	./scripts/prove.sh

# Proof on every supported Hermes release, one after the other (proof/ keeps one section per release).
prove-all:
	HERMES_TAG=v0.23.0 HERMES_SRC= ./scripts/prove.sh
	HERMES_TAG=v0.23.1 HERMES_SRC= ./scripts/prove.sh
