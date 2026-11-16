#!/usr/bin/env python3
"""Generate charts/myhealthcheck/dashboards/myhealthcheck.json.

The dashboard is generated (instead of edited by hand) so that panel ids, grid
positions and datasource references stay consistent. Run ``make dashboard``
after changing this file. The same JSON is used by docker-compose (provisioning)
and by the Helm chart (ConfigMap for the Grafana sidecar).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "charts/myhealthcheck/dashboards/myhealthcheck.json"

PROM = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
LOKI = {"type": "loki", "uid": "${DS_LOKI}"}
TEMPO = {"type": "tempo", "uid": "${DS_TEMPO}"}

SEL = 'namespace=~"$namespace", source=~"$source", name=~"$target"'
LOG_SEL = '{service_name="$service"}'

STATUS_MAPPINGS = [
    {
        "type": "value",
        "options": {
            "up": {"color": "green", "index": 0, "text": "UP"},
            "degraded": {"color": "orange", "index": 1, "text": "DEGRADED"},
            "down": {"color": "red", "index": 2, "text": "DOWN"},
            "unknown": {"color": "gray", "index": 3, "text": "UNKNOWN"},
        },
    }
]
UP_MAPPINGS = [
    {
        "type": "value",
        "options": {
            "0": {"color": "red", "index": 0, "text": "DOWN"},
            "1": {"color": "green", "index": 1, "text": "UP"},
        },
    }
]


class Builder:
    def __init__(self) -> None:
        self.panels: list[dict[str, Any]] = []
        self.y = 0
        self.next_id = 1

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id - 1

    def row(self, title: str) -> None:
        self.panels.append(
            {
                "type": "row",
                "id": self._id(),
                "title": title,
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y},
                "panels": [],
            }
        )
        self.y += 1

    def line(self, panels: list[tuple[int, int, dict[str, Any]]]) -> None:
        """Place panels side by side: (width, height, panel)."""
        x, height = 0, 0
        for width, h, panel in panels:
            panel["id"] = self._id()
            panel["gridPos"] = {"h": h, "w": width, "x": x, "y": self.y}
            self.panels.append(panel)
            x += width
            height = max(height, h)
        self.y += height


def prom(expr: str, legend: str = "", instant: bool = False, fmt: str = "time_series") -> dict:
    return {
        "datasource": PROM,
        "expr": expr,
        "legendFormat": legend,
        "instant": instant,
        "range": not instant,
        "format": fmt,
        "refId": "A",
    }


def stat(title: str, expr: str, color: str, unit: str = "short", desc: str = "") -> dict:
    return {
        "type": "stat",
        "title": title,
        "description": desc,
        "datasource": PROM,
        "targets": [prom(expr, instant=True)],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "color": {"mode": "fixed", "fixedColor": color},
                "noValue": "0",
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "value",
        },
    }


def timeseries(
    title: str, targets: list[dict], unit: str = "short", desc: str = "", stack: bool = False
) -> dict:
    for index, target in enumerate(targets):
        target["refId"] = chr(ord("A") + index)
    return {
        "type": "timeseries",
        "title": title,
        "description": desc,
        "datasource": targets[0]["datasource"],
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {
                    "drawStyle": "line",
                    "lineWidth": 1,
                    "fillOpacity": 15,
                    "showPoints": "never",
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                },
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull"]},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def build() -> dict[str, Any]:
    b = Builder()

    # ------------------------------------------------------------- overview
    b.row("Overview")
    b.line(
        [
            (4, 4, stat("Targets", 'sum(myhealthcheck_targets{source=~"$source"})', "blue")),
            (
                4,
                4,
                stat("Up", 'sum(myhealthcheck_targets{status="up", source=~"$source"})', "green"),
            ),
            (
                4,
                4,
                stat(
                    "Degraded",
                    'sum(myhealthcheck_targets{status="degraded", source=~"$source"})',
                    "orange",
                    desc="Answered correctly but slower than RESPONSE_TIME_THRESHOLD.",
                ),
            ),
            (
                4,
                4,
                stat("Down", 'sum(myhealthcheck_targets{status="down", source=~"$source"})', "red"),
            ),
            (
                4,
                4,
                stat(
                    "Last check cycle",
                    "time() - max(myhealthcheck_last_check_cycle_timestamp_seconds)",
                    "purple",
                    unit="s",
                    desc="Seconds since the last finished check cycle (CHECK_INTERVAL).",
                ),
            ),
            (
                4,
                4,
                {
                    **stat(
                        "Storage backend",
                        "max by (backend) (myhealthcheck_storage_backend) == 1",
                        "text",
                        desc="file = DB_PATH, temp = OS temp dir, memory = lost on restart.",
                    ),
                    "options": {
                        "colorMode": "none",
                        "graphMode": "none",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "textMode": "name",
                    },
                    "targets": [
                        prom(
                            "max by (backend) (myhealthcheck_storage_backend) == 1",
                            legend="{{backend}}",
                            instant=True,
                        )
                    ],
                },
            ),
        ]
    )

    # -------------------------------------------------------------- targets
    b.row("Targets")
    table = {
        "type": "table",
        "title": "Current status",
        "datasource": PROM,
        "targets": [
            {
                **prom(f"myhealthcheck_target_status{{{SEL}}} == 1", instant=True, fmt="table"),
                "refId": "A",
            },
            {
                **prom(
                    f"myhealthcheck_target_response_seconds{{{SEL}}}", instant=True, fmt="table"
                ),
                "refId": "B",
            },
        ],
        "transformations": [
            {"id": "merge", "options": {}},
            {
                "id": "organize",
                "options": {
                    "excludeByName": {
                        "Time": True,
                        "Value #A": True,
                        "__name__": True,
                        "instance": True,
                        "job": True,
                    },
                    "indexByName": {
                        "name": 0,
                        "namespace": 1,
                        "source": 2,
                        "status": 3,
                        "Value #B": 4,
                        "target": 5,
                    },
                    "renameByName": {
                        "name": "Name",
                        "namespace": "Namespace",
                        "source": "Source",
                        "status": "Status",
                        "target": "Target id",
                        "Value #B": "Response time",
                    },
                },
            },
        ],
        "fieldConfig": {
            "defaults": {"custom": {"filterable": True}},
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Status"},
                    "properties": [
                        {"id": "mappings", "value": STATUS_MAPPINGS},
                        {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                    ],
                },
                {
                    "matcher": {"id": "byName", "options": "Response time"},
                    "properties": [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 3}],
                },
            ],
        },
        "options": {"showHeader": True, "sortBy": [{"displayName": "Status", "desc": False}]},
    }
    timeline = {
        "type": "state-timeline",
        "title": "Availability timeline",
        "datasource": PROM,
        "targets": [prom(f"myhealthcheck_target_up{{{SEL}}}", legend="{{name}} ({{namespace}})")],
        "fieldConfig": {
            "defaults": {
                "mappings": UP_MAPPINGS,
                "color": {"mode": "thresholds"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}],
                },
            },
            "overrides": [],
        },
        "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.8},
    }
    b.line([(12, 10, table), (12, 10, timeline)])

    b.line(
        [
            (
                12,
                8,
                timeseries(
                    "Response time (last check)",
                    [prom(f"myhealthcheck_target_response_seconds{{{SEL}}}", "{{name}}")],
                    unit="s",
                ),
            ),
            (
                12,
                8,
                timeseries(
                    "Check latency p95",
                    [
                        prom(
                            "histogram_quantile(0.95, sum by (le, target) "
                            "(rate(myhealthcheck_check_duration_seconds_bucket"
                            '{source=~"$source"}[$__rate_interval])))',
                            "{{target}}",
                        )
                    ],
                    unit="s",
                ),
            ),
        ]
    )
    b.line(
        [
            (
                8,
                8,
                timeseries(
                    "Checks by result",
                    [
                        prom(
                            "sum by (status) (rate(myhealthcheck_checks_total"
                            '{source=~"$source"}[$__rate_interval]))',
                            "{{status}}",
                        )
                    ],
                    unit="ops",
                    stack=True,
                ),
            ),
            (
                8,
                8,
                timeseries(
                    "Alerts (status changes)",
                    [
                        prom(
                            "sum by (severity) (increase(myhealthcheck_alerts_total"
                            "[$__rate_interval]))",
                            "{{severity}}",
                        )
                    ],
                    stack=True,
                ),
            ),
            (
                8,
                8,
                timeseries(
                    "Check cycle duration p95",
                    [
                        prom(
                            "histogram_quantile(0.95, sum by (le) (rate("
                            "myhealthcheck_check_cycle_duration_seconds_bucket[$__rate_interval])))",
                            "p95",
                        )
                    ],
                    unit="s",
                ),
            ),
        ]
    )

    # ------------------------------------------------- discovery & api
    b.row("Kubernetes discovery & API")
    b.line(
        [
            (
                8,
                8,
                timeseries(
                    "Discovered targets",
                    [prom("max(myhealthcheck_discovered_targets)", "targets")],
                ),
            ),
            (
                8,
                8,
                timeseries(
                    "Discovery runs",
                    [
                        prom(
                            "sum by (result) (increase(myhealthcheck_discovery_runs_total"
                            "[$__rate_interval]))",
                            "{{result}}",
                        )
                    ],
                    stack=True,
                ),
            ),
            (
                8,
                8,
                timeseries(
                    "API requests",
                    [
                        prom(
                            "sum by (route, status_code) (rate("
                            "myhealthcheck_http_requests_total[$__rate_interval]))",
                            "{{route}} {{status_code}}",
                        )
                    ],
                    unit="reqps",
                ),
            ),
        ]
    )

    # ----------------------------------------------------------------- logs
    b.row("Logs (Loki)")
    log_rate = timeseries(
        "Log lines by level",
        [
            {
                "datasource": LOKI,
                "expr": f"sum by (level) (count_over_time({LOG_SEL} | json "
                '| level=~"$level" [$__auto]))',
                "legendFormat": "{{level}}",
                "queryType": "range",
            }
        ],
        stack=True,
    )
    log_rate["type"] = "timeseries"
    alerts_logs = {
        "type": "logs",
        "title": "Alerts (status changes)",
        "datasource": LOKI,
        "targets": [
            {
                "datasource": LOKI,
                "expr": f'{LOG_SEL} | json | event="alert"',
                "refId": "A",
                "queryType": "range",
            }
        ],
        "options": {
            "showTime": True,
            "wrapLogMessage": True,
            "prettifyLogMessage": False,
            "enableLogDetails": True,
            "sortOrder": "Descending",
        },
    }
    b.line([(12, 8, log_rate), (12, 8, alerts_logs)])
    b.line(
        [
            (
                24,
                12,
                {
                    "type": "logs",
                    "title": "myhealthcheck logs",
                    "description": "JSON logs. Expand a line and use the trace_id link "
                    "to open the trace in Tempo.",
                    "datasource": LOKI,
                    "targets": [
                        {
                            "datasource": LOKI,
                            "expr": f'{LOG_SEL} | json | level=~"$level" |~ "$search"',
                            "refId": "A",
                            "queryType": "range",
                        }
                    ],
                    "options": {
                        "showTime": True,
                        "wrapLogMessage": True,
                        "enableLogDetails": True,
                        "sortOrder": "Descending",
                    },
                },
            )
        ]
    )

    # --------------------------------------------------------------- traces
    b.row("Traces (Tempo)")
    b.line(
        [
            (
                12,
                10,
                {
                    "type": "table",
                    "title": "Recent traces",
                    "description": "Click a trace id to open it. Same data is in Jaeger.",
                    "datasource": TEMPO,
                    "targets": [
                        {
                            "datasource": TEMPO,
                            "queryType": "traceql",
                            "query": '{resource.service.name="$service"}',
                            "limit": 30,
                            "tableType": "traces",
                            "refId": "A",
                        }
                    ],
                    "fieldConfig": {"defaults": {}, "overrides": []},
                    "options": {"showHeader": True},
                },
            ),
            (
                12,
                10,
                {
                    "type": "table",
                    "title": "Slow or failed spans",
                    "datasource": TEMPO,
                    "targets": [
                        {
                            "datasource": TEMPO,
                            "queryType": "traceql",
                            "query": '{resource.service.name="$service" && '
                            '(status=error || duration>1s || span.myhealthcheck.status="down")}',
                            "limit": 30,
                            "tableType": "spans",
                            "refId": "A",
                        }
                    ],
                    "fieldConfig": {"defaults": {}, "overrides": []},
                    "options": {"showHeader": True},
                },
            ),
        ]
    )
    b.line(
        [
            (
                12,
                8,
                timeseries(
                    "Span rate (Tempo metrics-generator)",
                    [
                        prom(
                            "sum by (span_name) (rate(traces_spanmetrics_calls_total"
                            '{service="$service"}[$__rate_interval]))',
                            "{{span_name}}",
                        )
                    ],
                    unit="ops",
                    desc="Requires the Tempo metrics-generator (enabled in docker-compose).",
                ),
            ),
            (
                12,
                8,
                timeseries(
                    "Span latency p95 (Tempo metrics-generator)",
                    [
                        prom(
                            "histogram_quantile(0.95, sum by (le, span_name) (rate("
                            'traces_spanmetrics_latency_bucket{service="$service"}'
                            "[$__rate_interval])))",
                            "{{span_name}}",
                        )
                    ],
                    unit="s",
                ),
            ),
        ]
    )

    def ds_var(name: str, label: str, kind: str) -> dict[str, Any]:
        return {
            "name": name,
            "label": label,
            "type": "datasource",
            "query": kind,
            "current": {},
            "hide": 0,
            "refresh": 1,
            "regex": "",
            "options": [],
        }

    def label_var(name: str, label: str, query: str) -> dict[str, Any]:
        return {
            "name": name,
            "label": label,
            "type": "query",
            "datasource": PROM,
            "query": {"query": query, "refId": "A"},
            "definition": query,
            "includeAll": True,
            "multi": True,
            "allValue": ".*",
            "current": {"text": "All", "value": "$__all"},
            "refresh": 2,
            "sort": 1,
        }

    return {
        "title": "myhealthcheck",
        "uid": "myhealthcheck",
        "description": "Availability of monitored systems: metrics (Prometheus), "
        "logs (Loki) and traces (Tempo).",
        "tags": ["myhealthcheck", "health", "availability"],
        "editable": True,
        "graphTooltip": 1,
        "refresh": "30s",
        "schemaVersion": 41,
        "time": {"from": "now-3h", "to": "now"},
        "timezone": "browser",
        "links": [],
        "annotations": {"list": []},
        "panels": b.panels,
        "templating": {
            "list": [
                ds_var("DS_PROMETHEUS", "Prometheus", "prometheus"),
                ds_var("DS_LOKI", "Loki", "loki"),
                ds_var("DS_TEMPO", "Tempo", "tempo"),
                label_var(
                    "namespace", "Namespace", "label_values(myhealthcheck_target_up, namespace)"
                ),
                label_var("source", "Source", "label_values(myhealthcheck_target_up, source)"),
                label_var(
                    "target",
                    "Target",
                    'label_values(myhealthcheck_target_up{source=~"$source", '
                    'namespace=~"$namespace"}, name)',
                ),
                {
                    "name": "service",
                    "label": "Service (logs/traces)",
                    "type": "textbox",
                    "query": "myhealthcheck",
                    "current": {"text": "myhealthcheck", "value": "myhealthcheck"},
                },
                {
                    "name": "level",
                    "label": "Log level",
                    "type": "custom",
                    "query": "debug,info,warning,error,critical",
                    "includeAll": True,
                    "multi": True,
                    "allValue": ".*",
                    "current": {"text": "All", "value": "$__all"},
                },
                {
                    "name": "search",
                    "label": "Search logs",
                    "type": "textbox",
                    "query": "",
                    "current": {"text": "", "value": ""},
                },
            ]
        },
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
