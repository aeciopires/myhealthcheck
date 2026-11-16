"""Command line interface.

Examples::

    myhealthcheck                                   # API + periodic checks
    myhealthcheck -f .env -t config/targets.yaml    # custom files
    myhealthcheck -d -n default,apps -x 'kube-*'    # Kubernetes discovery
    myhealthcheck --once -u http://localhost:3000/health  # one cycle, JSON on stdout
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from myhealthcheck import __version__
from myhealthcheck.config import Settings, load_settings
from myhealthcheck.logging_config import configure_logging
from myhealthcheck.models import Status

logger = logging.getLogger("myhealthcheck.cli")

EXIT_OK = 0
EXIT_DOWN = 1
EXIT_CONFIG = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="myhealthcheck",
        description="Health check system with static targets and Kubernetes discovery.",
        epilog="Every option can also be set with environment variables "
        "(see docs/configuration.md).",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-f", "--env-file", type=Path, help="Path to a .env file")
    parser.add_argument(
        "-t",
        "-e",
        "--targets-file",
        type=Path,
        help="Targets file (.yaml/.yml/.json or plain text with one URL per line)",
    )
    parser.add_argument(
        "-u",
        "--url",
        action="append",
        dest="urls",
        metavar="URL",
        help="Endpoint to check (protocol://host:port/path). Repeatable.",
    )
    parser.add_argument(
        "--paths", help="Comma-separated default health paths (overrides DEFAULT_HEALTH_PATHS)"
    )
    parser.add_argument("-i", "--interval", type=float, help="Seconds between check cycles")
    parser.add_argument("-o", "--port", type=int, help="HTTP port of the API (default 8000)")
    parser.add_argument("--host", help="HTTP bind address (default 0.0.0.0)")
    parser.add_argument("--db-path", type=Path, help="SQLite database file")
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], type=str.upper
    )

    k8s = parser.add_argument_group("kubernetes discovery")
    k8s.add_argument(
        "-d", "--discovery", action="store_true", default=None, help="Enable discovery"
    )
    k8s.add_argument(
        "-p",
        "--in-cluster",
        action="store_true",
        default=None,
        help="Enable discovery using the in-cluster service account (inside a pod)",
    )
    k8s.add_argument("-n", "--namespaces", help="Comma-separated namespaces (globs allowed)")
    k8s.add_argument("-x", "--exclude-namespaces", help="Comma-separated namespaces to skip")
    k8s.add_argument("-l", "--label-selector", help="Service label selector, e.g. app=web")
    k8s.add_argument("--ports", help="Comma-separated service ports to check ('*' = all)")
    k8s.add_argument("-k", "--context", help="kubeconfig context (outside the cluster)")

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single check cycle, print a JSON report and exit (1 if any target is down)",
    )
    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    discovery = True if (args.discovery or args.in_cluster) else None
    overrides: dict[str, Any] = {
        "targets_file": args.targets_file,
        "health_endpoints": args.urls,
        "default_health_paths": args.paths,
        "check_interval": args.interval,
        "http_port": args.port,
        "http_host": args.host,
        "db_path": args.db_path,
        "log_level": args.log_level,
        "discovery_enabled": discovery,
        "k8s_in_cluster": True if args.in_cluster else None,
        "discovery_namespaces": args.namespaces,
        "discovery_exclude_namespaces": args.exclude_namespaces,
        "discovery_label_selector": args.label_selector,
        "discovery_ports": args.ports,
        "k8s_context": args.context,
    }
    return load_settings(args.env_file, **overrides)


async def run_once(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[int, dict[str, Any]]:
    """Run one discovery + check cycle and return ``(exit_code, report)``."""
    from myhealthcheck.api.app import build_monitor

    async with httpx.AsyncClient(transport=transport, verify=settings.verify_tls) as client:
        monitor = build_monitor(settings, client)
        try:
            await monitor.initialize()
            states = monitor.storage.list_targets()
            report = {
                "summary": monitor.storage.summary(),
                "targets": [s.model_dump(mode="json", exclude={"spec"}) for s in states],
            }
        finally:
            monitor.storage.close()
    down = any(s.status is Status.DOWN for s in states)
    return (EXIT_DOWN if down else EXIT_OK), report


def serve(settings: Settings) -> None:  # pragma: no cover - blocking server
    import uvicorn

    from myhealthcheck.api.app import create_app

    uvicorn.run(
        create_app(settings),
        host=settings.http_host,
        port=settings.http_port,
        log_config=None,
        access_log=False,
        proxy_headers=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = settings_from_args(args)
    except ValidationError as exc:
        configure_logging("INFO")
        logger.error("invalid configuration", extra={"errors": exc.errors(include_url=False)})
        return EXIT_CONFIG

    # In --once mode stdout carries the JSON report, so logs go to stderr.
    configure_logging(settings.log_level, stream=sys.stderr if args.once else None)
    logger.info(
        "configuration loaded",
        extra={"version": __version__, "settings": settings.public_dict()},
    )

    if args.once:
        code, report = asyncio.run(run_once(settings))
        sys.stdout.write(json.dumps(report, indent=2, default=str) + "\n")
        return code

    serve(settings)
    return EXIT_OK
