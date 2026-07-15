# cdpx — harness Makefile
# `make check` est LE portail qualité: rien ne se merge s'il ne passe pas.
# Convention: cibles idempotentes, sorties parlantes. Les tests unitaires sont
# strictement loopback; setup, images Docker et smoke packaging peuvent
# télécharger leurs dépendances explicites.

PY ?= python3
COV_MIN ?= 85

.PHONY: help setup check-local check lint fmt test test-e2e cov typecheck fixtures mock docker-build docker-check docker-e2e docker-symfony-e2e proof release clean dist smoke-dist

help: ## liste des cibles
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## installe le paquet en editable + outils dev (extras [dev])
	pip install -e ".[dev]" --break-system-packages --quiet || pip install -e ".[dev]"

check-local: lint typecheck test ## boucle locale: lint + format + mypy + tests unitaires
	@echo "== make check-local: OK =="

check: check-local docker-check docker-e2e docker-symfony-e2e ## PORTAIL QUALITÉ COMPLET: local + Docker + Chrome + Symfony
	@echo "== make check: OK =="

lint: ## ruff check + vérification de format
	ruff check src tests
	ruff format --check src tests

fmt: ## reformater le code
	ruff format src tests
	ruff check src tests --fix

test: ## tests unitaires déterministes (mock CDP + serveur fixtures, loopback only)
	$(PY) -m pytest tests --ignore=tests/e2e

test-e2e: ## e2e Chrome réel (M1) — échoue si Chrome/Chromium absent
	$(PY) -m pytest tests/e2e -v

cov: ## tests unitaires avec couverture (seuil bloquant, appliqué en CI)
	$(PY) -m pytest tests --ignore=tests/e2e --cov=cdpx --cov-report=term --cov-fail-under=$(COV_MIN)

typecheck: ## mypy sur src/cdpx (bloquant: inclus dans check depuis le vert durable)
	$(PY) -m mypy src/cdpx

docker-build: ## construire l'image portable cdpx-ci
	docker build -t cdpx-ci .

docker-check: docker-build ## make check-local dans l'image cdpx-ci
	docker run --rm cdpx-ci make check-local

docker-e2e: docker-build ## e2e Chrome réel dans l'image cdpx-ci
	docker run --rm cdpx-ci make test-e2e

docker-symfony-e2e: ## M2: e2e profiler contre une vraie app Symfony Dockerisée
	@set -eu; \
	mkdir -p .proof/evidence; \
	export CDPX_E2E_UID=$$(id -u) CDPX_E2E_GID=$$(id -g); \
	cleanup() { docker compose -f docker-compose.symfony-e2e.yml down --remove-orphans; }; \
	trap cleanup EXIT INT TERM; \
	cleanup; \
	docker compose -f docker-compose.symfony-e2e.yml up --build --abort-on-container-exit --exit-code-from cdpx

proof: ## rapport HTML humain basé sur les preuves collectées (.proof/)
	PYTHONPATH=src $(PY) -m cdpx.proof

release: check proof dist ## PORTAIL RELEASE: check complet + preuve + artefacts
	@echo "== make release: OK =="

fixtures: ## lancer le site témoin sur :8899 (inspection manuelle / e2e piloté main)
	$(PY) -m cdpx.testing.fixture_server --port 8899

mock: ## lancer un faux Chrome scriptable (debug du CLI sans navigateur)
	$(PY) -m cdpx.testing.mock_session

clean: ## nettoyer artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache .proof .proof.new .proof.old dist build src/*.egg-info
	find src tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

dist: check-local ## wheel + sdist vérifiés (le portail release impose check complet)
	rm -rf dist
	$(PY) -m build
	$(PY) -m twine check --strict dist/*
	PYTHONPATH=src $(PY) scripts/verify_dist.py
	$(MAKE) smoke-dist

smoke-dist: ## installer le wheel en environnement propre et vérifier métadonnées + CLI
	@set -eu; \
	venv=$$(mktemp -d); \
	cleanup() { rm -rf "$$venv"; }; \
	trap cleanup EXIT INT TERM; \
	$(PY) -m venv "$$venv"; \
	"$$venv/bin/python" -m pip install --disable-pip-version-check --quiet dist/*.whl; \
	"$$venv/bin/cdpx" --version; \
	"$$venv/bin/cdpx" --help >/dev/null; \
	"$$venv/bin/python" -c 'from importlib.metadata import metadata; from cdpx.cli import build_parser; from cdpx.proof import parse_help_commands; m = metadata("cdpx"); assert m["License-Expression"] == "MIT"; assert m["License-File"] == "LICENSE"; assert len(parse_help_commands(build_parser().format_help())) == 31'
