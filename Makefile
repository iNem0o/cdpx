# cdpx — harness Makefile
# `make check` is THE quality gate: nothing merges if it does not pass.
# Targets are idempotent and their output stays readable. Unit tests are
# strictly loopback-only; setup, Docker images and packaging smoke tests may
# download their explicit dependencies.

PY ?= python3
COV_MIN ?= 85

.PHONY: help setup check-local check lint fmt test test-e2e cov typecheck fixtures mock site-casts docker-build docker-check docker-e2e docker-symfony-e2e proof release clean dist smoke-dist

help: ## list available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## install the editable package and development tools
	pip install -e ".[dev]" --break-system-packages --quiet || pip install -e ".[dev]"

check-local: lint typecheck test ## short loop: lint, format, mypy and unit tests
	@echo "== make check-local: OK =="

check: check-local docker-check docker-e2e docker-symfony-e2e ## FULL QUALITY GATE: local + Docker + Chrome + Symfony
	@echo "== make check: OK =="

lint: ## run Ruff and verify formatting
	ruff check src tests
	ruff format --check src tests

fmt: ## format code and apply safe Ruff fixes
	ruff format src tests
	ruff check src tests --fix

test: ## deterministic unit tests (CDP mock + fixture server, loopback only)
	$(PY) -m pytest tests --ignore=tests/e2e

test-e2e: ## real Chrome e2e; fails if Chrome or Chromium is absent
	$(PY) -m pytest tests/e2e -v

cov: ## unit tests with coverage (blocking threshold, enforced in CI)
	$(PY) -m pytest tests --ignore=tests/e2e --cov=cdpx --cov-report=term --cov-fail-under=$(COV_MIN)

typecheck: ## run the blocking mypy check on src/cdpx
	$(PY) -m mypy src/cdpx

docker-build: ## build the portable cdpx-ci image
	docker build -t cdpx-ci .

docker-check: docker-build ## run make check-local inside cdpx-ci
	docker run --rm cdpx-ci make check-local

docker-e2e: docker-build ## real Chrome e2e inside the cdpx-ci image
	docker run --rm cdpx-ci make test-e2e

# CDPX_PROOF_DIR is pinned for both compose commands (cleanup down and up):
# a leftover user export must never redirect the mount, the container applies
# a recursive chown -R to it.
docker-symfony-e2e: ## profiler e2e against a real Dockerized Symfony app
	@set -eu; \
	mkdir -p .proof/evidence; \
	export CDPX_E2E_UID=$$(id -u) CDPX_E2E_GID=$$(id -g) CDPX_PROOF_DIR=./.proof; \
	cleanup() { docker compose -f docker-compose.symfony-e2e.yml down --remove-orphans --volumes; }; \
	trap cleanup EXIT INT TERM; \
	cleanup; \
	docker compose -f docker-compose.symfony-e2e.yml up --build --abort-on-container-exit --exit-code-from cdpx

proof: ## human HTML report based on the collected proofs (.proof/)
	PYTHONPATH=src $(PY) -m cdpx.proof

release: check proof dist ## release gate: full check, proof and distributions
	@echo "== make release: OK =="

fixtures: ## serve the reference site on :8899 (manual inspection / hand-driven e2e)
	$(PY) -m cdpx.testing.fixture_server --port 8899

mock: ## launch a scriptable fake Chrome (CLI debugging without a browser)
	$(PY) -m cdpx.testing.mock_session

# Dedicated compose project: never interferes with the docker-symfony-e2e
# state, and `down --volumes` avoids leaking the reference app's anonymous
# volumes.
SITE_CASTS_COMPOSE = docker compose -p cdpx-site-casts \
	-f docker-compose.symfony-e2e.yml -f docker-compose.site-casts.yml

site-casts: ## (re)record the homepage tutorial casts (real Chrome + Symfony app)
	$(SITE_CASTS_COMPOSE) up -d --wait symfony
	$(PY) scripts/site_casts/generate.py record --symfony-base http://127.0.0.1:8025; \
	status=$$?; $(SITE_CASTS_COMPOSE) down --volumes --remove-orphans; exit $$status
	$(PY) scripts/site_casts/generate.py check

clean: ## remove generated artifacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache .proof .proof.new .proof.old dist build src/*.egg-info
	find src tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

dist: check-local ## verified wheel + sdist (the release gate requires the full check)
	rm -rf dist
	$(PY) -m build
	$(PY) -m twine check --strict dist/*
	PYTHONPATH=src $(PY) scripts/verify_dist.py
	$(MAKE) smoke-dist

smoke-dist: ## install the wheel in a clean environment and verify metadata + CLI
	@set -eu; \
	venv=$$(mktemp -d); \
	cleanup() { rm -rf "$$venv"; }; \
	trap cleanup EXIT INT TERM; \
	$(PY) -m venv "$$venv"; \
	env -u PYTHONPATH "$$venv/bin/python" -m pip install --disable-pip-version-check --quiet dist/*.whl; \
	env -u PYTHONPATH "$$venv/bin/cdpx" --version; \
	env -u PYTHONPATH "$$venv/bin/cdpx" --help >/dev/null; \
	env -u PYTHONPATH "$$venv/bin/python" -c 'from importlib.metadata import metadata; from cdpx.cli import build_parser; from cdpx.proof import parse_help_commands; m = metadata("cdpx"); assert m["License-Expression"] == "MIT"; assert m["License-File"] == "LICENSE"; assert len(parse_help_commands(build_parser().format_help())) == 31'
