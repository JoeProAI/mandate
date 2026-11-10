HERMES_SRC ?= $(or $(XDG_CACHE_HOME),$(HOME)/.cache)/mandate-hermes/v0.23.0
PY := $(HERMES_SRC)/.venv/bin/python
HERMES := $(HERMES_SRC)/.venv/bin/hermes
export HERMES_SRC

.PHONY: hermes validate test prove

hermes:
	./scripts/install_hermes.sh

validate: hermes
	$(HERMES) plugins validate . --install-deps

test: hermes
	$(PY) -m pytest
