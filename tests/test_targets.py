import json
from pathlib import Path

import pytest

from myhealthcheck.models import MatchMode, Source
from myhealthcheck.targets import (
    TargetsFileError,
    dedupe,
    load_static_targets,
    load_targets_file,
    normalize_url,
    split_url,
    target_from_url,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://a:80/health", "http://a:80/health"),
        ("https:10.0.0.5:8443/status", "https://10.0.0.5:8443/status"),
        ("HTTP:svc.ns:8080/x", "http://svc.ns:8080/x"),
        ("localhost:8000/health", "http://localhost:8000/health"),
        ("  http://a  ", "http://a"),
    ],
)
def test_normalize_url(value: str, expected: str) -> None:
    assert normalize_url(value) == expected


def test_target_from_url_with_and_without_path() -> None:
    with_path = target_from_url("http:10.1.2.3:8080/status")
    assert with_path.base_url == "http://10.1.2.3:8080"
    assert with_path.paths == ["/status"]
    assert with_path.name == "10.1.2.3:8080/status"
    without = target_from_url("http://svc:3000/", source=Source.API)
    assert without.paths == []
    assert without.source is Source.API


def test_split_url_prefix_rules() -> None:
    assert split_url("http://svc:80/health") == ("http://svc:80", ["/health"], "svc:80/health")
    assert split_url("http://svc:80/") == ("http://svc:80", [], "svc:80")
    assert split_url("http://svc/app/", ["/health"]) == ("http://svc/app", ["/health"], "svc/app")


def test_yaml_url_with_path_and_prefix(tmp_path: Path) -> None:
    file = tmp_path / "t.yaml"
    file.write_text(
        "targets:\n"
        "  - url: http://alloy:12345/-/ready\n"
        "  - url: http://app:80/prefix\n"
        "    paths: [/health]\n"
    )
    ready, prefixed = load_targets_file(file)
    assert (ready.base_url, ready.paths) == ("http://alloy:12345", ["/-/ready"])
    assert prefixed.url_for("/health") == "http://app:80/prefix/health"


def test_plain_text_file(tmp_path: Path) -> None:
    file = tmp_path / "endpoints.txt"
    file.write_text("# comment\nhttp://a:1/health\n\nhttps:b:2/status  # inline\nftp://bad\nc:3\n")
    targets = load_targets_file(file)
    assert [t.base_url for t in targets] == ["http://a:1", "https://b:2", "http://c:3"]


def test_kube_pires_v2_demo_target_uses_readiness() -> None:
    config_path = Path(__file__).parents[1] / "config" / "targets.yaml"

    demo, *_ = load_targets_file(config_path)

    assert demo.name == "kube-pires"
    assert demo.base_url == "http://kube-pires:3000"
    assert demo.paths == ["/readyz"]
    assert demo.expected_body == '"status":"ready"'


def test_yaml_file_with_defaults(tmp_path: Path) -> None:
    file = tmp_path / "targets.yaml"
    file.write_text(
        """
defaults:
  paths: [/health]
  expected_status: [200]
targets:
  - name: api
    url: http:api:8080
    match: all
    paths: [/live, /ready]
    namespace: ignored
  - base_url: http://web:80
  - http://plain:9000/status
"""
    )
    api, web, plain = load_targets_file(file)
    assert api.name == "api"
    assert api.base_url == "http://api:8080"
    assert api.match is MatchMode.ALL
    assert api.paths == ["/live", "/ready"]
    assert api.namespace is None
    assert api.expected_status == [200]
    assert web.name == "web:80"
    assert web.paths == ["/health"]
    assert plain.paths == ["/status"]


def test_json_file_list_form(tmp_path: Path) -> None:
    file = tmp_path / "targets.json"
    file.write_text(json.dumps([{"name": "x", "url": "http://x:1"}]))
    assert [t.name for t in load_targets_file(file)] == ["x"]


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        (".yaml", "targets: [1]"),
        (".yaml", "just a string"),
        (".yaml", "defaults: [1]\ntargets: []"),
        (".yaml", "targets:\n  - name: x\n    url: ftp://x"),
        (".yaml", "targets: [unclosed"),
        (".json", "{bad json"),
    ],
)
def test_invalid_files(tmp_path: Path, suffix: str, content: str) -> None:
    file = tmp_path / f"targets{suffix}"
    file.write_text(content)
    with pytest.raises(TargetsFileError):
        load_targets_file(file)


def test_empty_and_missing_files(tmp_path: Path) -> None:
    empty = tmp_path / "empty.yml"
    empty.write_text("")
    assert load_targets_file(empty) == []
    assert load_targets_file(tmp_path / "missing.yaml") == []


def test_load_static_targets_merges_and_dedupes(tmp_path: Path) -> None:
    file = tmp_path / "t.txt"
    file.write_text("http://a:1/health\n")
    targets = load_static_targets(["http://a:1/health", "http://b:2", "ftp://bad"], file)
    assert [t.base_url for t in targets] == ["http://a:1", "http://b:2"]
    assert load_static_targets([], None) == []
    assert len(dedupe([targets[0], targets[0]])) == 1
