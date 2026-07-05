# cdpx — harness Makefile
# `make check` est LE portail qualité: rien ne se merge s'il ne passe pas.
# Convention: cibles idempotentes, sorties parlantes, zéro dépendance réseau
# externe pour check/test (tout tourne sur loopback).

PY ?= python3

.PHONY: help setup check lint fmt test test-e2e fixtures mock docker-build docker-check docker-e2e docker-symfony-e2e proof clean dist

help: ## liste des cibles
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## installe le paquet en editable + outils dev
	pip install -e . --break-system-packages --quiet || pip install -e .
	pip install pytest ruff --break-system-packages --quiet || pip install pytest ruff

check: lint test ## PORTAIL QUALITÉ: lint + format + tests unitaires
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

docker-build: ## construire l'image portable cdpx-ci
	docker build -t cdpx-ci .

docker-check: docker-build ## make check dans l'image cdpx-ci
	docker run --rm cdpx-ci make check

docker-e2e: docker-build ## e2e Chrome réel dans l'image cdpx-ci
	docker run --rm cdpx-ci make test-e2e

docker-symfony-e2e: ## M2: e2e profiler contre une vraie app Symfony Dockerisée
	docker compose -f docker-compose.symfony-e2e.yml up --build --abort-on-container-exit --exit-code-from cdpx
	docker compose -f docker-compose.symfony-e2e.yml down --remove-orphans

proof: ## rapport HTML humain basé sur les preuves collectées (.proof/)
	PYTHONPATH=src $(PY) -m cdpx.proof

fixtures: ## lancer le site témoin sur :8899 (inspection manuelle / e2e piloté main)
	$(PY) -m cdpx.testing.fixture_server --port 8899

mock: ## lancer un faux Chrome scriptable (debug du CLI sans navigateur)
	$(PY) -m cdpx.testing.mock_cdp

clean: ## nettoyer artefacts
	rm -rf .pytest_cache .ruff_cache dist build src/*.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

dist: check ## archive distribuable (après check)
	mkdir -p dist && tar --exclude .git --exclude dist -czf dist/cdpx.tar.gz .
