# Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Every discovered target is `down` with `ConnectError` | Running outside the cluster: `*.svc` names do not resolve | Run inside the cluster, or use static targets / port-forward |
| A Service is not discovered | Port not in `DISCOVERY_PORTS` (default 80, 8080, 8888) | Add the port, use `DISCOVERY_PORTS=*` or annotate `myhealthcheck.io/port` |
| A namespace is ignored | It matches `DISCOVERY_EXCLUDE_NAMESPACES` (globs!) | Check `GET /api/v1/config` |
| `cannot list namespaces: ... forbidden` | RBAC without ClusterRole but namespaces empty/globs | `rbac.clusterWide=true` or list namespaces explicitly |
| `cannot list services in namespace` (warning) | Role missing in that namespace | Add the namespace to `config.discovery.namespaces` and upgrade the release |
| Target `down` with `unexpected status code 404` on every path | The app has no health endpoint among the defaults | Set `paths` (file/API) or the `myhealthcheck.io/paths` annotation |
| Target `degraded` | Slower than `RESPONSE_TIME_THRESHOLD` | Investigate the app or raise the threshold |
| `storage_backend` is `temp` or `memory` | `DB_PATH` not writable | Fix permissions/volume; see [storage.md](storage.md) |
| `/health/ready` returns 503 for long | First cycle still running (many targets, long timeouts) | Lower `REQUEST_TIMEOUT`/`RETRY_ATTEMPTS`, raise `MAX_CONCURRENCY` |
| No traces in Tempo/Jaeger | `OTEL_ENABLED` false or wrong endpoint | `OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318` (HTTP, not gRPC port) |
| No logs in Grafana | Alloy cannot read the Docker socket | `docker compose logs alloy`; on macOS make sure Docker Desktop exposes `/var/run/docker.sock` |
| `docker compose up`: port is already allocated | Another stack uses the port | `GRAFANA_PORT=3300 MHC_PORT=8800 make up` (see `docker-compose.yml` header) |
| `This chart requires Helm 4` | Helm 3 in PATH | `make install-deps` and use `mise exec -- helm ...` |
| Invalid configuration on start (exit code 2) | Bad env var value | The JSON log lists the field and the error |

Debug tips:

```bash
LOG_LEVEL=DEBUG uv run myhealthcheck --once -u http://my-app:8080   # see every attempt
curl -s localhost:8000/api/v1/targets/<id>/history?limit=5 | jq
curl -s localhost:8000/metrics | grep myhealthcheck_target_up
```
