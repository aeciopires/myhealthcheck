#!/usr/bin/env bash
# Shared helpers for scripts/check-deps.sh and scripts/install-deps.sh.
# Compatible with bash 3.2 (macOS default shell).

SUPPORTED_UBUNTU="22.04 24.04 26.04"

if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_RESET=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_RESET=""
fi

info() { printf '%s[INFO]%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }
ok() { printf '%s[ OK ]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
fail() { printf '%s[FAIL]%s %s\n' "$C_RED" "$C_RESET" "$*"; }

has() { command -v "$1" >/dev/null 2>&1; }

# Prints "linux" or "darwin".
os_name() { uname -s | tr '[:upper:]' '[:lower:]'; }

# Prints "amd64" or "arm64" (or the raw value when unknown).
arch_name() {
  case "$(uname -m)" in
    x86_64 | amd64) echo amd64 ;;
    arm64 | aarch64) echo arm64 ;;
    *) uname -m ;;
  esac
}

# Returns 0 when the platform is officially supported, prints a warning otherwise.
check_platform() {
  local os arch
  os="$(os_name)"
  arch="$(arch_name)"
  case "$os" in
    linux)
      if [ ! -r /etc/os-release ]; then
        warn "cannot read /etc/os-release; supported Linux: Ubuntu ${SUPPORTED_UBUNTU} (amd64)"
        return 1
      fi
      # shellcheck disable=SC1091
      . /etc/os-release
      if [ "${ID:-}" != "ubuntu" ] || ! echo " ${SUPPORTED_UBUNTU} " | grep -q " ${VERSION_ID:-none} "; then
        warn "${PRETTY_NAME:-unknown Linux} is not officially supported (Ubuntu ${SUPPORTED_UBUNTU})"
        return 1
      fi
      if [ "$arch" != "amd64" ]; then
        warn "Ubuntu is supported on amd64 only (detected ${arch})"
        return 1
      fi
      ok "platform: ${PRETTY_NAME} ${arch}"
      ;;
    darwin)
      if [ "$arch" != "amd64" ] && [ "$arch" != "arm64" ]; then
        warn "macOS is supported on amd64 and arm64 (detected ${arch})"
        return 1
      fi
      ok "platform: macOS $(sw_vers -productVersion 2>/dev/null || echo '?') ${arch}"
      ;;
    *)
      warn "unsupported operating system: ${os}"
      return 1
      ;;
  esac
  return 0
}

# Locate mise even when it is not yet in PATH (fresh install).
find_mise() {
  if has mise; then
    command -v mise
  elif [ -x "${HOME}/.local/bin/mise" ]; then
    echo "${HOME}/.local/bin/mise"
  elif [ -x /opt/homebrew/bin/mise ]; then
    echo /opt/homebrew/bin/mise
  elif [ -x /usr/local/bin/mise ]; then
    echo /usr/local/bin/mise
  fi
}
