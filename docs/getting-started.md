# Getting started

<!-- TOC -->

- [1. Install the dependencies](#1-install-the-dependencies)
- [2. Option A - full stack with Docker Compose](#2-option-a---full-stack-with-docker-compose)
- [3. Option B - run with Python](#3-option-b---run-with-python)
- [4. Option C - Kubernetes with Helm 4](#4-option-c---kubernetes-with-helm-4)
- [5. Try the API](#5-try-the-api)

<!-- TOC -->

## 1. Install the dependencies

Install Docker yourself (see [REQUIREMENTS.md](../REQUIREMENTS.md)), then:

```bash
make install-deps   # mise + Python 3.15 + uv + helm 4 + kubectl + kind + linters + .venv
make check-deps     # verifies everything
```

## 2. Option A - full stack with Docker Compose

```bash
make up            # docker compose up --build -d
```

| What | URL |
|---|---|
| Swagger UI | http://localhost:8000/docs |
| Grafana (dashboard "myhealthcheck") | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Jaeger | http://localhost:16686 |
| Alloy UI | http://localhost:12345 |

The stack runs the demo image `aeciopires/kube-pires:2.0.0`, checking its `/readyz`
readiness endpoint, **and** every observability component (Prometheus, Grafana, Loki,
Tempo, Jaeger, Alloy). Edit [config/targets.yaml](../config/targets.yaml) and restart
`myhealthcheck` to add your own systems.

Busy ports? Override them: `GRAFANA_PORT=3300 MHC_PORT=8800 make up`.

```bash
make logs ARGS=myhealthcheck   # JSON logs
make down                      # stop (add ARGS=-v to delete volumes)
```

## 3. Option B - run with Python

```bash
cp config/env.example .env                      # optional
make run ARGS="-f .env -t config/targets.yaml"   # API on :8000 + periodic checks

# One cycle, JSON report on stdout, exit code 1 if anything is down (great for CI/cron):
make run-once ARGS="-u https://example.com -u http:localhost:8000/health/live"
```

`uv run myhealthcheck --help` lists every flag.

## 4. Option C - Kubernetes with Helm 4

```bash
make kind-deploy           # local kind cluster + image + chart
# or on a real cluster:
helm upgrade --install myhealthcheck charts/myhealthcheck -n myhealthcheck --create-namespace
kubectl -n myhealthcheck port-forward svc/myhealthcheck 8000:8000
```

By default discovery scans **all namespaces** except `kube-system`, `kube-public` and
`kube-node-lease`, on service ports **80, 8080 and 8888**. See
[kubernetes-discovery.md](kubernetes-discovery.md).

## 5. Try the API

```bash
curl -s localhost:8000/api/v1/summary | jq
curl -s localhost:8000/api/v1/targets | jq '.[] | {name, status, matched_path}'
curl -s -XPOST localhost:8000/api/v1/targets -H 'content-type: application/json' \
  -d '{"url": "https://example.com", "paths": ["/"]}' | jq
curl -s -XPOST localhost:8000/api/v1/probe -H 'content-type: application/json' \
  -d '{"url": "http:localhost:8000/status"}' | jq
```
