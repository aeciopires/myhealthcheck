# REST API

Interactive documentation is generated from the code by FastAPI:

* Swagger UI: `http://<host>:8000/docs`
* ReDoc: `http://<host>:8000/redoc`
* OpenAPI document: `http://<host>:8000/openapi.json` (a copy is kept in [openapi.json](openapi.json),
  regenerated with `make openapi`)

Analogy: the API is the **reception desk**. You can ask who the patients are and how they
are doing, admit a new patient, discharge one, or ask the nurse to check someone right now.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/targets?status=&source=&namespace=` | List systems with their endpoints and latest status |
| `POST` | `/api/v1/targets?check_now=true` | Register a system (`source=api`) |
| `GET` | `/api/v1/targets/{id}` | One system |
| `DELETE` | `/api/v1/targets/{id}` | Stop monitoring a system |
| `GET` | `/api/v1/targets/{id}/history?limit=50` | Check history (newest first) |
| `POST` | `/api/v1/targets/{id}/check` | Check one system now |
| `POST` | `/api/v1/checks` | Run a full check cycle now |
| `POST` | `/api/v1/probe` | Ad-hoc check of any URL (not stored) |
| `POST` | `/api/v1/discovery` | Run Kubernetes discovery now (`409` if disabled, `502` on API errors) |
| `GET` | `/api/v1/summary` | Counters by status/source, storage backend, last cycle |
| `GET` | `/api/v1/config` | Effective settings (secrets masked) |
| `GET` | `/health/live` | Liveness probe |
| `GET` | `/health/ready` | Readiness probe (`503` until the first cycle ends) |
| `GET` | `/health`, `/status` | Health aliases (2.x compatible) |
| `GET` | `/metrics` | Prometheus metrics |

## Examples

```bash
# Register: the URL path becomes the health path
curl -s -XPOST localhost:8000/api/v1/targets -H 'content-type: application/json' -d '{
  "name": "erp", "url": "https:erp.internal:8443/api/health",
  "expected_status": [200], "labels": {"team": "finance"}
}'

# Register with several paths, all must answer
curl -s -XPOST localhost:8000/api/v1/targets -H 'content-type: application/json' -d '{
  "url": "http://shop:8080", "paths": ["/live", "/ready"], "match": "all"
}'

# Only the systems that are down
curl -s 'localhost:8000/api/v1/targets?status=down' | jq '.[].name'
```

### Target object (response)

```json
{
  "id": "kubernetes-apps-kube-pires-80",
  "name": "kube-pires:80",
  "source": "kubernetes",
  "namespace": "apps",
  "base_url": "http://kube-pires.apps.svc:80",
  "paths": [],
  "match": "any",
  "labels": {"app": "kube-pires"},
  "status": "up",
  "matched_path": "/health",
  "response_time": 0.0042,
  "last_checked": "2026-11-16T10:32:23.703868Z",
  "last_change": "2026-11-16T10:31:54.533000Z",
  "endpoints": [
    {"url": "http://kube-pires.apps.svc:80/status", "path": "/status", "status": "down",
     "status_code": 404, "response_time": 0.003, "attempts": 1,
     "error": "unexpected status code 404", "checked_at": "..."},
    {"url": "http://kube-pires.apps.svc:80/health", "path": "/health", "status": "up",
     "status_code": 200, "response_time": 0.004, "attempts": 1, "error": null, "checked_at": "..."}
  ],
  "spec": {"...": "the full definition used for the checks"}
}
```

`id` is stable: `<source>-<namespace>-<name>` (slugified).

## Alert webhook payload

When `ALERT_WEBHOOK_URL` is set, every status change is POSTed as JSON:

```json
{
  "event": "alert", "severity": "critical", "target": "static-erp", "target_name": "erp",
  "namespace": null, "source": "static", "base_url": "https://erp.internal:8443",
  "previous_status": "up", "status": "down", "errors": ["timeout"],
  "checked_at": "2026-11-16T10:30:00+00:00"
}
```

`severity`: `critical` (down), `warning` (degraded), `resolved` (up).
