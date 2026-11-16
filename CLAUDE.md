# CLAUDE.md

Maintenance notes for AI assistants (and humans) working on this repository.

## What this is

`myhealthcheck`: Python 3.15 service that checks the availability of other applications.
Targets come from a file / `HEALTH_ENDPOINTS` / REST API and from Kubernetes Service discovery.
Results go to SQLite, Prometheus metrics, JSON logs and OpenTelemetry traces.
FastAPI serves the API (`/docs`), probes (`/health/live`, `/health/ready`) and `/metrics`.

## Commands (always through make / mise)

```bash
make install-deps   # mise tools + .venv (Docker is installed separately)
make check-deps
make format lint test      # ruff, mypy --strict, shellcheck, hadolint, yamllint, helm lint; pytest >= 90% cov
make up / make down        # compose stack (app + Grafana, Loki, Tempo, Prometheus, Jaeger, Alloy)
make kind-deploy           # kind cluster + chart
make helm-docs dashboard openapi   # regenerate generated files
```

Tools are pinned in `mise.toml`; run them with `mise exec -- <tool>` (Makefile does this).
Never use a system `helm` 3 - the chart fails on Helm < 4 on purpose.

## Layout

* `src/myhealthcheck/` - `config.py` (Settings), `models.py`, `targets.py`, `discovery.py`,
  `checker.py`, `storage.py`, `monitor.py` (orchestrator), `notifier.py`, `metrics.py`,
  `logging_config.py`, `telemetry.py`, `api/` (app factory, routes, schemas), `cli.py`.
* `tests/` - pytest; fakes in `tests/conftest.py` (`routes_transport`, `FakeCoreV1Api`, `k8s_service`).
* `charts/myhealthcheck/` - Helm 4 chart; `dashboards/myhealthcheck.json` is shared with compose.
* `observability/` - configs of the compose observability stack.
* `scripts/` - `check-deps.sh`, `install-deps.sh`, `lib.sh` (bash 3.2 compatible), `gen_dashboard.py`.
* `docs/` - user/maintainer docs (English, with mermaid diagrams and analogies).

## Invariants and gotchas

* **Settings**: every option lives in `config.Settings` with a `validation_alias` (legacy 2.x names
  are kept as extra aliases). Lists are CSV in env vars (`NoDecode` + `split_csv`). `"*"`/`"all"` = empty list.
* **Defaults that users rely on**: `CHECK_INTERVAL=60`, default paths start with `/status`, `/health`;
  `DISCOVERY_PORTS=80,8080,8888`; excluded namespaces `kube-system,kube-public,kube-node-lease`.
* **URL rules** (`targets.split_url`): without `paths` the URL path is the health path; with `paths`
  the URL path is a prefix. Short form `http:host:port/path` and bare `host:port` are accepted.
* **Retries** only for network errors, timeouts and 5xx; never for 4xx.
* **Match `any`** probes paths in order and stops at the first healthy one; the last healthy path
  (`matched_path`) is tried first next time.
* **Alerts** only on status transitions (`Notifier.should_alert`); `unknown -> up` is silent.
* **Startup is non-blocking**: `Monitor.start()` spawns the first cycle; `/health/ready` is 503 until it ends.
* **Storage fallback**: `DB_PATH` -> `$TMPDIR/myhealthcheck/<file>` -> `:memory:`. All SQLite access
  goes through `Storage` (RLock) and is called with `asyncio.to_thread`.
* **Target ids** are `slugify(source, namespace, name)`; static/kubernetes targets are synced per
  source (`Storage.sync_source`) and removed ones must also call `Metrics.forget_target`.
* **Logging**: JSON formatter adds `trace_id`/`span_id`. Never use reserved `LogRecord` attribute
  names as `extra` keys (use `target_name`, not `name`) - it raises `KeyError`.
* **httpx drops default ports** (`:80`) from URLs; the test transport normalizes keys for that.
* **Metrics** use an app-owned `CollectorRegistry` (no global registry).
* **Tracing** is off unless `OTEL_ENABLED=true`; standard `OTEL_*` env vars configure the exporter (OTLP/HTTP).
* **Helm chart**: `_helpers.tpl` enforces Helm 4; ClusterRole name includes the namespace;
  `rbac.clusterWide=false` creates Roles per listed namespace and refuses globs; the container runs
  as UID 10001 with read-only root FS (`/data` and `/tmp` are volumes). Run `make helm-docs` after
  changing `values.yaml` (comments with `# --` become the README table).
* **Dashboard** JSON is generated: edit `scripts/gen_dashboard.py`, then `make dashboard`.
  It uses datasource variables (`DS_PROMETHEUS`, `DS_LOKI`, `DS_TEMPO`).
* **Compose**: Alloy only collects logs of the compose project `myhealthcheck`; host ports are
  overridable (`GRAFANA_PORT`, `MHC_PORT`, ...). Tempo 3.x config has no `traces_storage`/`local-blocks`.
* `Makefile` must stay compatible with GNU make 3.81 (macOS): no `.ONESHELL`, no `!=`.

## When changing things

1. Code + tests (keep coverage >= 90%, currently ~99%).
2. `make format lint test`.
3. Update docs: `docs/*.md`, `README.md`, `config/env.example`, chart `values.yaml` (+ `make helm-docs`),
   `CHANGELOG.md`. Regenerate `docs/openapi.json` (`make openapi`) when the API changes.
4. Versions: see the table in `docs/development.md#updating-versions`.
