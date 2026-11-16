"""REST API (FastAPI) of myhealthcheck."""

from myhealthcheck.api.app import build_monitor, create_app

__all__ = ["build_monitor", "create_app"]
