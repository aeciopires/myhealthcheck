# changelog
<!-- TOC -->

- [changelog](#changelog)
- [3.0.0](#300)
- [2.1.1](#211)
- [2.1.0](#210)
- [2.0.0](#200)
- [1.1.0](#110)
- [1.0.0](#100)


<!-- TOC -->

# 3.0.0

Complete refactoring. Chart 1.0.0. **Breaking changes** - see docs/configuration.md#migrating-from-2x.

Application

- Rewritten as the installable package `myhealthcheck` (`src/` layout, `pyproject.toml`, `uv.lock`), Python 3.15.
- FastAPI REST API documented with Swagger/OpenAPI (`/docs`, `/redoc`, `/openapi.json`): list systems with
  endpoints and status, register/remove targets, check now, ad-hoc probe, run discovery, history, summary, config.
- Health endpoints `/health/live`, `/health/ready` (503 until the first cycle), `/health` and `/status`.
- Static targets from YAML/JSON/text files and `HEALTH_ENDPOINTS`, accepting `protocol://host:port/endpoint`,
  `protocol:host:port/endpoint` and `host:port`.
- Default health paths (`/status`, `/health`, `/healthz`, `/livez`, `/readyz`, `/ready`, `/ping`, `/-/healthy`,
  `/-/ready`, `/api/health`, `/health/live`, `/health/ready`, `/actuator/health`, `/q/health`, `/_health`,
  `/healthcheck`), customizable with `DEFAULT_HEALTH_PATHS` or per target.
- Kubernetes discovery: namespace list, all namespaces, exclusion list, glob patterns, label selector,
  ports filter (default 80, 8080, 8888, customizable), Service annotations `myhealthcheck.io/*`.
- Default check interval is 60 seconds (`CHECK_INTERVAL`).
- Retries only for network errors, timeouts and 5xx; expected status/body; degraded status for slow answers.
- SQLite storage with history and fallback `DB_PATH` -> temp dir -> memory.
- JSON logs with `trace_id`/`span_id`; OpenTelemetry traces (OTLP/HTTP); new Prometheus metrics.
- Alerts only on status change (log + optional webhook `ALERT_WEBHOOK_URL`).
- `--once` mode for CI/cron with meaningful exit codes.
- Unit tests with ~99% coverage (minimum 90% enforced).

Fixed bugs of 2.x

- Each endpoint started its own infinite loop that checked *all* endpoints (N x N requests).
- Non-JSON health responses (e.g. plain `OK`) raised inside the check and were counted as failed attempts and retried.
- `/metrics` was hand-written text with leading indentation instead of the standard exposition format.
- The image baked the local `app/.env` file into the container.
- Discovery called `list_namespace` once per namespace and needed cluster permissions even for fixed namespaces.
- Configuration was parsed at import time, making the module impossible to test.

Helm chart 1.0.0

- Renamed to `myhealthcheck` and moved to `charts/myhealthcheck`; requires **Helm 4**.
- Fixed: `image.tag` did not default to `appVersion`; probes used `ps | grep`; container command ran
  through `/bin/sh -c`; RBAC granted `create` and was tied to `serviceAccount.create`; ClusterRole name
  could collide between namespaces; NOTES hardcoded port 8000; no configuration was passed to the app.
- New: ConfigMap-based configuration, targets file, HTTP probes (startup/liveness/readiness), non-root
  read-only container, `rbac.clusterWide` (ClusterRole or Roles per namespace), PVC persistence,
  webhook Secret, ServiceMonitor, PrometheusRule, Grafana dashboard ConfigMap, `values.schema.json`, `helm test`.

Tooling and observability

- `mise.toml` pins Python, uv, Helm 4, kubectl, kind, helm-docs, hadolint, shellcheck, yamllint, trivy.
- Makefile: `check-deps`, `install-deps` (Ubuntu 22.04/24.04/26.04 amd64, macOS amd64/arm64), lint, test,
  compose, helm, kind, image, scan targets.
- Docker Compose stack: Grafana 13.3.1, Loki 3.7.10, Tempo 3.1.1, Prometheus 3.16.0, Jaeger 2.23.0 and
  Grafana Alloy 1.22.0 integrated for metrics, logs and traces, with provisioned datasources and dashboard.
- Multi-stage Dockerfile (python:3.15.2-slim-trixie + uv), non-root, HEALTHCHECK, amd64/arm64.
- Documentation in `docs/`, `REQUIREMENTS.md` and `CLAUDE.md`.

# 2.1.1

- Fix JSON message

# 2.1.0

- Adjusted arguments and documentation
- Added code tests/analisys using sonarqube
- Added /metrics endpoint supported by Prometheus
- Added /health endpoint supported by kubernetes

# 2.0.0

- Created helm chart for deploy on kubernetes
- Upgraded version of script and Docker image.
- Added -d option set k8s discovery.
- Added -e option to load endpoints.txt file path.
- Added -k option set k8s cluster name.
- Added -h option to show help about the all options of the program.
- Added -l option set k8s label service.
- Added -n option set k8s namespace.
- Added -p option set k8s discovery on pod.
- Put on log all informations about the k8s configuration and configution file loaded.
- If -l or K8S_LABEL_SELECTOR not passed do service discovey of all service in the namespace.
- If -n or K8S_NAMESPACE not passed do service discovey of all service in all namespaces.

# 1.1.0

- Upgraded version of script and Docker image.
- Removed ``requests`` dependency package.
- Added ``aiohttp`` dependency package.
- Implemented a retry mechanism for transient failures.
- Used async IO to handle multiple endpoints in parallel, if the system needs to scale.
- Added new environment variables: ``RETRY_ATTEMPTS``, ``RETRY_DELAY`` and ``HEALTH_ENDPOINTS``.
- Removed old environment variable: ``HEALTH_ENDPOINT``.
- Created the ``CHANGELOG.md`` file.

# 1.0.0

- Initial version of the health-check system.
