# myhealthcheck - developer tasks.
# Compatible with GNU make 3.81+ (macOS default) and bash 3.2+.
# Run "make" or "make help" to list the targets.

SHELL := /bin/bash
.DEFAULT_GOAL := help

VERSION        ?= $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
IMAGE_REPO     ?= aeciopires/myhealthcheck
IMAGE          ?= $(IMAGE_REPO):$(VERSION)
PLATFORMS      ?= linux/amd64,linux/arm64
CHART_DIR      ?= charts/myhealthcheck
KIND_CLUSTER   ?= myhealthcheck
NAMESPACE      ?= myhealthcheck
ARGS           ?=

# Run the tools pinned in mise.toml even when mise is not activated in the shell.
MISE           ?= $(shell command -v mise 2>/dev/null || echo $(HOME)/.local/bin/mise)
RUN            := $(MISE) exec --
UV             := $(RUN) uv
COMPOSE        ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

.PHONY: help check-deps install-deps sync format lint lint-python lint-shell lint-docker \
	lint-yaml typecheck test coverage run run-once dashboard image image-multiarch scan \
	openapi up down logs ps restart helm-lint helm-template helm-docs helm-package kind-up \
	kind-deploy kind-down clean

help: ## Show this help
	@echo "myhealthcheck $(VERSION) - available targets:"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------- dependencies
check-deps: ## Check that all required software is installed
	@bash scripts/check-deps.sh

install-deps: ## Install mise, the pinned tools and the Python venv (Docker is NOT installed)
	@bash scripts/install-deps.sh

sync: ## Create/update the Python virtualenv (.venv) from uv.lock
	$(UV) sync --frozen

# ------------------------------------------------------------------- quality
format: ## Format Python code
	$(UV) run ruff format src tests scripts
	$(UV) run ruff check --fix src tests scripts

lint: lint-python typecheck lint-shell lint-docker lint-yaml helm-lint ## Run every linter

lint-python: ## Ruff (lint + format check)
	$(UV) run ruff check src tests scripts
	$(UV) run ruff format --check src tests scripts

typecheck: ## mypy (strict)
	$(UV) run mypy src

lint-shell: ## shellcheck
	$(RUN) shellcheck -x scripts/*.sh

lint-docker: ## hadolint
	$(RUN) hadolint Dockerfile

lint-yaml: ## yamllint
	$(RUN) yamllint -c .yamllint.yaml .

test: ## Run unit tests with coverage (fails under 90%)
	$(UV) run pytest --cov --cov-report=term-missing --cov-report=xml

coverage: ## HTML coverage report in htmlcov/
	$(UV) run pytest --cov --cov-report=html
	@echo "open htmlcov/index.html"

# ----------------------------------------------------------------------- run
run: ## Run the API + checks locally (ARGS="-t config/targets.yaml")
	$(UV) run myhealthcheck $(ARGS)

run-once: ## One check cycle, JSON report on stdout (ARGS="-u http://localhost:3001/health")
	$(UV) run myhealthcheck --once $(ARGS)

dashboard: ## Regenerate the Grafana dashboard JSON
	$(UV) run python scripts/gen_dashboard.py

openapi: ## Export the OpenAPI document to docs/openapi.json
	$(UV) run python -c "import json; from myhealthcheck.api import create_app; \
		from myhealthcheck.config import Settings; \
		print(json.dumps(create_app(Settings()).openapi(), indent=2))" > docs/openapi.json

# ---------------------------------------------------------------- container
image: ## Build the Docker image for the local platform
	docker build --build-arg VERSION=$(VERSION) -t $(IMAGE) -t $(IMAGE_REPO):latest .

image-multiarch: ## Build and push a multi-arch image (requires docker login)
	docker buildx inspect multi-platform-builder >/dev/null 2>&1 || \
		docker buildx create --name multi-platform-builder --platform $(PLATFORMS)
	docker buildx build --builder multi-platform-builder --push --platform $(PLATFORMS) \
		--build-arg VERSION=$(VERSION) -t $(IMAGE) -t $(IMAGE_REPO):latest .

scan: image ## Scan the image for vulnerabilities with Trivy
	$(RUN) trivy image --severity HIGH,CRITICAL --ignore-unfixed $(IMAGE)

# ------------------------------------------------------------ docker compose
up: ## Start app + Grafana/Loki/Tempo/Prometheus/Jaeger/Alloy
	$(COMPOSE) up --build -d
	@echo ""
	@echo "API/Swagger: http://localhost:$${MHC_PORT:-8000}/docs"
	@echo "Grafana:     http://localhost:$${GRAFANA_PORT:-3000}"
	@echo "Prometheus:  http://localhost:$${PROMETHEUS_PORT:-9090}"
	@echo "Jaeger:      http://localhost:$${JAEGER_PORT:-16686}"

down: ## Stop the stack (keeps volumes; "make down ARGS=-v" drops them)
	$(COMPOSE) down $(ARGS)

logs: ## Follow logs of the stack (ARGS=myhealthcheck for one service)
	$(COMPOSE) logs -f $(ARGS)

ps: ## Show the stack containers
	$(COMPOSE) ps

restart: down up ## Recreate the stack

# --------------------------------------------------------------------- helm
helm-lint: ## Lint and render the Helm chart (Helm 4)
	$(RUN) helm lint $(CHART_DIR) --strict
	$(RUN) helm template ci $(CHART_DIR) >/dev/null
	$(RUN) helm template ci $(CHART_DIR) --set rbac.clusterWide=false \
		--set 'config.discovery.namespaces={default}' --set persistence.enabled=true \
		--set serviceMonitor.enabled=true --set prometheusRule.enabled=true \
		--set grafanaDashboard.enabled=true --set ingress.enabled=true >/dev/null

helm-template: ## Render the chart to stdout (ARGS="--set ...")
	$(RUN) helm template myhealthcheck $(CHART_DIR) -n $(NAMESPACE) $(ARGS)

helm-docs: ## Regenerate charts/myhealthcheck/README.md
	$(RUN) helm-docs --chart-search-root charts --template-files README.md.gotmpl

helm-package: ## Package the chart into dist/
	$(RUN) helm package $(CHART_DIR) -d dist

# --------------------------------------------------------------------- kind
kind-up: ## Create a local kind cluster
	$(RUN) kind get clusters | grep -qx $(KIND_CLUSTER) || \
		$(RUN) kind create cluster --name $(KIND_CLUSTER) --wait 120s

kind-deploy: image kind-up ## Build, load the image into kind and install the chart
	$(RUN) kind load docker-image $(IMAGE) --name $(KIND_CLUSTER)
	$(RUN) helm upgrade --install myhealthcheck $(CHART_DIR) -n $(NAMESPACE) --create-namespace \
		--kube-context kind-$(KIND_CLUSTER) --set image.tag=$(VERSION) --set image.pullPolicy=Never \
		--wait --timeout 3m $(ARGS)

kind-down: ## Delete the local kind cluster
	$(RUN) kind delete cluster --name $(KIND_CLUSTER)

# -------------------------------------------------------------------- misc
clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml .coverage dist build
	find src tests scripts -type d -name __pycache__ -prune -exec rm -rf {} +
