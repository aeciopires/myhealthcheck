"""Load static targets from ``HEALTH_ENDPOINTS`` and from a targets file.

Supported file formats:

* ``.yaml`` / ``.yml`` / ``.json`` - structured file (see ``config/targets.yaml``)::

      defaults:
        paths: [/health]
      targets:
        - name: kube-pires
          url: http://kube-pires:3000
          paths: [/health]
          expected_body: "It's working"

* anything else - plain text, one URL per line, ``#`` starts a comment
  (format of the old ``endpoints.txt``).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError

from myhealthcheck.models import Source, TargetSpec

logger = logging.getLogger(__name__)


class TargetsFileError(ValueError):
    """Raised when a targets file cannot be parsed."""


_SHORT_FORM_RE = re.compile(r"^(?P<scheme>https?):(?!//)(?P<rest>.+)$", re.IGNORECASE)


def normalize_url(value: str) -> str:
    """Accept the forms ``protocol://host:port/endpoint``,
    ``protocol:host:port/endpoint`` and ``host:port/endpoint`` (http assumed).

    ``host`` may be an IP address or a DNS name.
    """
    value = value.strip()
    if match := _SHORT_FORM_RE.match(value):
        return f"{match['scheme'].lower()}://{match['rest']}"
    if "://" not in value:
        return f"http://{value}"
    return value


def split_url(url: str, paths: list[str] | None = None) -> tuple[str, list[str], str]:
    """Split ``url`` into ``(base_url, paths, default_name)``.

    * no ``paths``: the URL path becomes the health path
      (``http://svc:8080/health`` -> ``http://svc:8080`` + ``["/health"]``);
      a URL without path gets the default paths later.
    * with ``paths``: the URL path is kept as a prefix of every path
      (``http://svc/app`` + ``["/health"]`` -> ``http://svc/app/health``).
    """
    parts = urlsplit(normalize_url(url))
    url_path = parts.path.rstrip("/")
    root = f"{parts.scheme}://{parts.netloc}"
    if paths:
        return root + url_path, list(paths), parts.netloc + url_path
    return root, [url_path] if url_path else [], parts.netloc + url_path


def target_from_url(url: str, source: Source = Source.STATIC) -> TargetSpec:
    """Convert a full URL into a target (see :func:`split_url`)."""
    base_url, paths, name = split_url(url)
    return TargetSpec(name=name, base_url=base_url, paths=paths, source=source)


def _parse_text(content: str) -> list[TargetSpec]:
    targets = []
    for lineno, raw in enumerate(content.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            targets.append(target_from_url(line))
        except ValidationError as exc:
            logger.warning(
                "ignoring invalid URL in targets file",
                extra={"line": lineno, "value": line, "error": str(exc.errors()[0]["msg"])},
            )
    return targets


def _parse_structured(data: Any) -> list[TargetSpec]:
    if data is None:
        return []
    if isinstance(data, list):
        data = {"targets": data}
    if not isinstance(data, dict):
        raise TargetsFileError("targets file must be a mapping or a list")

    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise TargetsFileError("'defaults' must be a mapping")

    targets = []
    for index, item in enumerate(data.get("targets") or []):
        if isinstance(item, str):
            targets.append(target_from_url(item))
            continue
        if not isinstance(item, dict):
            raise TargetsFileError(f"target #{index} must be a mapping or a URL string")
        merged = {**defaults, **item}
        if "url" in merged and "base_url" not in merged:
            merged["base_url"] = merged.pop("url")
        if isinstance(merged.get("base_url"), str):
            paths = merged.get("paths")
            base_url, split_paths, name = split_url(
                merged["base_url"], paths if isinstance(paths, list) else None
            )
            merged.update(base_url=base_url, paths=split_paths)
            merged.setdefault("name", name)
        merged["source"] = Source.STATIC
        merged.pop("namespace", None)
        try:
            targets.append(TargetSpec.model_validate(merged))
        except ValidationError as exc:
            raise TargetsFileError(f"invalid target #{index}: {exc}") from exc
    return targets


def load_targets_file(path: Path) -> list[TargetSpec]:
    """Parse a targets file; a missing file returns an empty list."""
    if not path.is_file():
        logger.warning("targets file not found", extra={"path": str(path)})
        return []
    content = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    try:
        if suffix in {".yaml", ".yml"}:
            return _parse_structured(yaml.safe_load(content))
        if suffix == ".json":
            return _parse_structured(json.loads(content))
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise TargetsFileError(f"cannot parse {path}: {exc}") from exc
    return _parse_text(content)


def dedupe(targets: Iterable[TargetSpec]) -> list[TargetSpec]:
    """Keep the first target for every id."""
    seen: dict[str, TargetSpec] = {}
    for target in targets:
        seen.setdefault(target.id, target)
    return list(seen.values())


def load_static_targets(endpoints: list[str], targets_file: Path | None) -> list[TargetSpec]:
    """Merge ``HEALTH_ENDPOINTS`` URLs and the targets file (file wins on conflicts)."""
    from_file = load_targets_file(targets_file) if targets_file else []
    from_env = []
    for url in endpoints:
        try:
            from_env.append(target_from_url(url))
        except ValidationError:
            logger.warning("ignoring invalid URL in HEALTH_ENDPOINTS", extra={"value": url})
    return dedupe([*from_file, *from_env])
