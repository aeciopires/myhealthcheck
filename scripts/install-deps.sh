#!/usr/bin/env bash
# Install everything needed to develop/run myhealthcheck, except Docker.
#
#   * Ubuntu 22.04 / 24.04 / 26.04 (amd64): base packages with apt
#   * macOS (amd64 / arm64): Xcode Command Line Tools (+ Homebrew packages if available)
#   * mise (https://mise.jdx.dev) + every tool pinned in mise.toml
#   * Python virtualenv (.venv) with uv
#
# Usage: scripts/install-deps.sh   (or: make install-deps)

set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/lib.sh
. scripts/lib.sh

if ! check_platform; then
  warn "continuing anyway; things may not work as expected"
fi

sudo_cmd() {
  if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}

install_linux_packages() {
  info "installing base packages with apt (sudo may ask for your password)"
  sudo_cmd apt-get update -y
  sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl git make gnupg jq sqlite3 tar xz-utils unzip
}

install_macos_packages() {
  if ! xcode-select -p >/dev/null 2>&1; then
    warn "Xcode Command Line Tools not found; starting installer (re-run this script afterwards)"
    xcode-select --install || true
    exit 1
  fi
  ok "Xcode Command Line Tools"
  if has brew; then
    info "installing helper packages with Homebrew"
    brew install jq sqlite curl git
  else
    warn "Homebrew not found: skipping jq/sqlite (optional). See https://brew.sh"
  fi
}

case "$(os_name)" in
  linux) install_linux_packages ;;
  darwin) install_macos_packages ;;
  *) fail "unsupported OS"; exit 1 ;;
esac

MISE="$(find_mise)"
if [ -z "$MISE" ]; then
  info "installing mise (https://mise.run)"
  curl -fsSL https://mise.run | sh
  MISE="${HOME}/.local/bin/mise"
fi
ok "mise $("$MISE" --version | awk '{print $1}')"

info "installing tools pinned in mise.toml"
"$MISE" trust --yes mise.toml
"$MISE" install --yes

info "creating the Python virtualenv (.venv) with uv"
"$MISE" exec -- uv sync --frozen

if ! has docker; then
  warn "Docker is not installed. Install it separately: https://docs.docker.com/engine/install/"
fi

cat <<EOF

${C_GREEN}Done.${C_RESET} To use the pinned tools directly in your shell, activate mise once:

  bash: echo 'eval "\$(${MISE} activate bash)"' >> ~/.bashrc
  zsh:  echo 'eval "\$(${MISE} activate zsh)"'  >> ~/.zshrc

Then run: make check-deps
EOF
