.PHONY: help all setup deps build fmt fmt-check lint test coverage ci dialyzer e2e claude-setup claude-test

MIX ?= mix

help:
	@echo "Targets: setup, deps, fmt, fmt-check, lint, test, coverage, dialyzer, e2e, claude-setup, claude-test, ci"

setup:
	$(MIX) setup

claude-setup:
	npm --prefix ../runner/claude ci --ignore-scripts --no-audit --no-fund

claude-test:
	npm --prefix ../runner/claude run check
	npm --prefix ../runner/claude test

deps:
	$(MIX) deps.get

build:
	$(MIX) build

fmt:
	$(MIX) format

fmt-check:
	$(MIX) format --check-formatted

lint:
	$(MIX) lint

coverage:
	$(MIX) test --cover

test:
	$(MIX) test

dialyzer:
	$(MIX) deps.get
	$(MIX) dialyzer --format short

e2e:
	SYMPHONY_RUN_LIVE_E2E=1 $(MIX) test test/symphony_elixir/live_e2e_test.exs

ci:
	$(MAKE) setup
	$(MAKE) build
	$(MAKE) fmt-check
	$(MAKE) lint
	$(MAKE) claude-setup
	$(MAKE) claude-test
	$(MAKE) coverage
	$(MAKE) dialyzer

all: ci
