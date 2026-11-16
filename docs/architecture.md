# Architecture

<!-- TOC -->

- [Layers](#layers)
- [Modules](#modules)
- [Startup sequence](#startup-sequence)
- [One check cycle](#one-check-cycle)
- [How a target is checked](#how-a-target-is-checked)
- [Status model](#status-model)
- [Design decisions](#design-decisions)

<!-- TOC -->

## Layers

Analogy: a **restaurant**. The *waiters* (API) take orders, the *kitchen manager*
(Monitor) organizes the work, the *cooks* (checker, discovery) do the actual work and
the *pantry* (storage) keeps everything. Waiters never cook, cooks never talk to clients.

```mermaid
flowchart TB
    subgraph Entry["Entry points"]
        CLI["cli.py<br/>argparse, --once"]
        API["api/<br/>FastAPI routes + OpenAPI"]
    end
    subgraph Core["Orchestration"]
        MON["monitor.py<br/>loops, sync, alerts"]
    end
    subgraph Workers["Workers"]
        CHK["checker.py<br/>HTTP checks + retries"]
        DIS["discovery.py<br/>Kubernetes Services"]
        TGT["targets.py<br/>static files / URLs"]
        NOT["notifier.py<br/>alerts"]
    end
    subgraph Infra["Infrastructure"]
        STO["storage.py<br/>SQLite + fallback"]
        MET["metrics.py<br/>Prometheus"]
        LOG["logging_config.py<br/>JSON logs"]
        TEL["telemetry.py<br/>OpenTelemetry"]
    end
    CFG["config.py<br/>Settings (env / .env / CLI)"]
    MOD["models.py<br/>TargetSpec, TargetCheck, Status"]

    CLI --> API
    CLI --> MON
    API --> MON
    MON --> CHK & DIS & TGT & NOT & STO & MET
    CFG -.-> CLI & API & MON
    MOD -.-> CHK & DIS & STO & API
```

## Modules

| Module | Responsibility | Analogy |
|---|---|---|
| `config.py` | Pydantic `Settings`; env vars, `.env`, legacy aliases | The house rules |
| `models.py` | `TargetSpec` (what to check), `EndpointResult`, `TargetCheck`, `TargetState`, `Status` | The forms everybody fills |
| `targets.py` | Parse `targets.yaml/.json/.txt`, `HEALTH_ENDPOINTS`, URL normalization | The appointment book |
| `discovery.py` | List Services in namespaces, filter ports, read annotations | The census taker |
| `checker.py` | HTTP GET with timeout, retries, body/status validation, path probing | The nurse |
| `storage.py` | SQLite tables `targets` and `check_history`, fallback file -> temp -> memory | The records cabinet |
| `monitor.py` | Glue: loads targets, runs loops, records results, triggers alerts | The head nurse |
| `notifier.py` | Alert only on status **change** (log + optional webhook) | The pager |
| `metrics.py` | Prometheus metrics with their own registry | The hallway board |
| `logging_config.py` | One JSON object per log line with `trace_id`/`span_id` | The diary |
| `telemetry.py` | OpenTelemetry tracer, FastAPI/httpx instrumentation | The GPS tracker |
| `api/` | FastAPI app factory, routes, schemas | The reception desk |
| `cli.py` | Command line, `--once` mode, starts uvicorn | The front door |

## Startup sequence

```mermaid
sequenceDiagram
    autonumber
    participant U as uvicorn
    participant A as FastAPI lifespan
    participant M as Monitor
    participant S as Storage
    participant D as Discovery
    U->>A: start
    A->>S: open DB (file -> temp -> memory)
    A->>M: start()
    M-->>A: returns immediately
    Note over U: /health/live = 200<br/>/health/ready = 503
    par background
        M->>M: load static targets
        M->>D: discover (if enabled)
        M->>M: first check cycle
        Note over U: /health/ready = 200
    and
        loop every CHECK_INTERVAL
            M->>M: run_checks()
        end
    and
        loop every DISCOVERY_INTERVAL
            M->>D: run_discovery()
        end
    end
```

The server answers liveness immediately; readiness only turns green after the first
cycle. This prevents Kubernetes from killing a pod that is still doing its first
(possibly slow) round.

## One check cycle

```mermaid
sequenceDiagram
    participant M as Monitor
    participant S as Storage
    participant C as Checker
    participant T as Target
    participant P as Prometheus metrics
    participant N as Notifier
    M->>S: list_specs()
    par for each target (max MAX_CONCURRENCY)
        M->>C: check_target(spec, preferred_path)
        C->>T: GET base_url + path
        T-->>C: status code, body, latency
        C-->>M: TargetCheck
        M->>S: save_check() -> previous status
        M->>P: record_check()
        alt status changed
            M->>N: notify()
        end
    end
    M->>P: targets by source/status, cycle duration
```

## How a target is checked

```mermaid
flowchart TD
    S[Target] --> Q{match mode}
    Q -- all --> ALL[Check every path concurrently]
    Q -- any --> ORD[Order paths: last healthy path first]
    ORD --> P1[Check next path]
    P1 --> R{status?}
    R -- up/degraded --> STOP[Stop: matched_path = this path]
    R -- down --> MORE{more paths?}
    MORE -- yes --> P1
    MORE -- no --> DOWN[Target DOWN]
    ALL --> COMB[combine_status]

    subgraph Endpoint["Each endpoint (with retries)"]
        G[GET url] --> E{result}
        E -- network error / timeout / 5xx --> RETRY{attempts left?}
        RETRY -- yes --> W[sleep RETRY_DELAY] --> G
        RETRY -- no --> D2[DOWN]
        E -- 4xx or unexpected status/body --> D3[DOWN, no retry]
        E -- OK but slow --> DEG[DEGRADED]
        E -- OK --> UP[UP]
    end
```

Why no retry on `404`? Retrying cannot fix "this path does not exist"; it only wastes
time while probing the default paths. Analogy: if a door does not exist, knocking
three times won't create it.

## Status model

| Status | Meaning |
|---|---|
| `unknown` | Never checked yet |
| `up` | Expected status code (and body, if configured) within the threshold |
| `degraded` | Correct answer but slower than `RESPONSE_TIME_THRESHOLD`, or (mode `all`) only some paths healthy |
| `down` | Network error, timeout, unexpected status or body |

```mermaid
stateDiagram-v2
    [*] --> unknown
    unknown --> up: first check OK (no alert)
    unknown --> down: alert critical
    unknown --> degraded: alert warning
    up --> down: alert critical
    up --> degraded: alert warning
    degraded --> up: alert resolved
    degraded --> down: alert critical
    down --> up: alert resolved
    down --> degraded: alert warning
```

## Design decisions

* **asyncio + httpx**: one process checks thousands of endpoints concurrently with little memory.
* **Kubernetes client is synchronous** and runs in `asyncio.to_thread` to avoid blocking the loop.
* **SQLite** (stdlib) keeps state and history without an external database; access is serialized
  by a lock and executed in threads.
* **Composition root** (`api/app.py::build_monitor`): objects receive their dependencies, which
  makes unit tests easy (fake transports, fake Kubernetes API).
* **Metrics registry per app**: no global state, tests do not interfere with each other.
* **Alerts on transitions only**: avoids alert storms.
* **Stable target ids** (`<source>-<namespace>-<name>`) so history survives restarts.
