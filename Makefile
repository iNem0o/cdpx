# Internal compatibility facade. Host workflows use ./dev; these aliases run
# inside the digest-pinned development image.

PY ?= python
HARNESS = $(PY) -m tools.harness
WORKTREE_ROOT := $(realpath $(CURDIR))
WORKTREE_ID := $(shell printf '%s' "$(WORKTREE_ROOT)" | sha256sum | cut -c1-12)
DEV_IMAGE ?= cdpx-dev:wt-$(WORKTREE_ID)
RUNTIME_IMAGE ?= cdpx-runtime:wt-$(WORKTREE_ID)
COMPOSE_PROJECT ?= cdpx-gate-$(WORKTREE_ID)

.PHONY: help setup check-local check lint fmt test test-e2e cov typecheck fixtures mock site-casts docker-build docker-check docker-e2e docker-symfony-e2e proof release clean dist smoke-dist

help:
	@./dev help

setup:
	@echo "Host setup is Docker-only: run ./dev setup" >&2

check-local:
	$(HARNESS) check-local

check:
	$(HARNESS) check

lint:
	ruff check src tests tools
	ruff format --check src tests tools

fmt:
	$(HARNESS) fmt

test:
	$(PY) -m pytest tests --ignore=tests/e2e

test-e2e:
	$(HARNESS) test-e2e

cov:
	$(PY) -m pytest tests --ignore=tests/e2e --cov=cdpx --cov-branch --cov-fail-under=85

typecheck:
	$(PY) -m mypy src/cdpx tools

fixtures:
	$(PY) -m cdpx.testing.fixture_server --port 8899

mock:
	$(PY) -m cdpx.testing.mock_session

site-casts:
	@echo "Run ./dev site-record from the host Docker portal." >&2
	@exit 2

docker-build:
	docker buildx bake --load --set dev.tags=$(DEV_IMAGE) --set runtime.tags=$(RUNTIME_IMAGE) dev runtime

docker-check:
	docker run --rm $(DEV_IMAGE) $(HARNESS) check-local

docker-e2e:
	docker run --rm --env CDPX_E2E_IMAGE=$(DEV_IMAGE) $(DEV_IMAGE) $(HARNESS) test-e2e

docker-symfony-e2e:
	docker compose -p $(COMPOSE_PROJECT) -f docker-compose.symfony-e2e.yml up --build --abort-on-container-exit --exit-code-from cdpx; \
	status=$$?; \
	docker compose -p $(COMPOSE_PROJECT) -f docker-compose.symfony-e2e.yml down --volumes --remove-orphans; \
	exit $$status

proof:
	$(HARNESS) proof

release:
	$(HARNESS) release

dist:
	uv build --wheel --out-dir dist

smoke-dist:
	@echo "The wheel is an internal image-build artifact; validate the runtime image instead."

clean:
	$(HARNESS) clean
