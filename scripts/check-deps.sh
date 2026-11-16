#!/usr/bin/env bash
# Check that every tool needed to develop/run myhealthcheck is installed.
# Usage: scripts/check-deps.sh   (or: make check-deps)
# Exit code: 0 = everything found, 1 = something is missing.

set -u
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=scripts/lib.sh
. scripts/lib.sh

missing=0

info "checking platform"
check_platform || true

info "checking system tools"
for tool in git make curl; do
  if has "$tool"; then ok "$tool"; else fail "$tool not found"; missing=1; fi
done

info "checking Docker (installed separately, see REQUIREMENTS.md)"
if has docker; then
  ok "docker $(docker version --format '{{.Client.Version}}' 2>/dev/null || echo '(daemon not reachable)')"
else
  fail "docker not found"
  missing=1
fi
if has docker && docker compose version >/dev/null 2>&1; then
  ok "docker compose $(docker compose version --short 2>/dev/null)"
elif has docker-compose; then
  ok "docker-compose $(docker-compose version --short 2>/dev/null)"
else
  fail "docker compose (plugin) / docker-compose not found"
  missing=1
fi

info "checking mise and the tools pinned in mise.toml"
MISE="$(find_mise)"
if [ -z "$MISE" ]; then
  fail "mise not found (run: make install-deps)"
  exit 1
fi
ok "mise $("$MISE" --version 2>/dev/null | awk '{print $1}')"

if [ -n "$("$MISE" ls --current --missing 2>/dev/null)" ]; then
  fail "tools missing from mise.toml:"
  "$MISE" ls --current --missing
  missing=1
fi

check_tool() { # <binary> <version command...>
  local bin="$1"
  shift
  local out
  if out="$("$MISE" exec -- "$@" 2>/dev/null | head -n 1)" && [ -n "$out" ]; then
    ok "${bin}: ${out}"
  else
    fail "${bin} not available through mise"
    missing=1
  fi
}

check_tool python python --version
check_tool uv uv --version
check_tool helm helm version --short
check_tool kubectl kubectl version --client
check_tool helm-docs helm-docs --version
check_tool kind kind version
check_tool hadolint hadolint --version
check_tool shellcheck shellcheck --version
check_tool yamllint yamllint --version
check_tool trivy trivy --version

helm_version="$("$MISE" exec -- helm version --short 2>/dev/null || true)"
case "$helm_version" in
  v4.*) ok "Helm 4 detected" ;;
  *) fail "Helm 4 is required (found: ${helm_version:-none})"; missing=1 ;;
esac

info "checking Python virtualenv"
if [ -x .venv/bin/python ] && .venv/bin/python -c "import myhealthcheck" 2>/dev/null; then
  ok ".venv ready ($(.venv/bin/python --version))"
else
  warn ".venv not ready (run: make install-deps or make sync)"
fi

if [ "$missing" -ne 0 ]; then
  fail "some dependencies are missing - run: make install-deps"
  exit 1
fi
ok "all dependencies found"
