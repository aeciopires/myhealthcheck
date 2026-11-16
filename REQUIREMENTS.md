# Requirements

<!-- TOC -->

- [Supported platforms](#supported-platforms)
- [Software](#software)
- [1. Install Docker (manually)](#1-install-docker-manually)
- [2. Install everything else](#2-install-everything-else)
- [3. Activate mise in your shell (optional)](#3-activate-mise-in-your-shell-optional)
- [4. Check](#4-check)
- [Manual installation](#manual-installation)

<!-- TOC -->

## Supported platforms

| OS | Architecture |
|---|---|
| Ubuntu 22.04 LTS | amd64 |
| Ubuntu 24.04 LTS | amd64 |
| Ubuntu 26.04 LTS | amd64 |
| macOS (14+) | amd64 (Intel) and arm64 (Apple Silicon) |

The Docker image is built for `linux/amd64` and `linux/arm64`.

## Software

| Software | Version | Installed by | Used for |
|---|---|---|---|
| Docker Engine / Docker Desktop | 27+ | **you** (see below) | images, compose stack, kind |
| Docker Compose plugin | v2+ | **you** | local stack |
| git, curl, make, bash | any | apt / Xcode CLT | basic tooling |
| [mise](https://mise.jdx.dev) | latest | `make install-deps` | installs the tools below |
| Python | 3.15.2 | mise | application |
| uv | 0.13.8 | mise | Python dependencies (`uv.lock`) |
| Helm | **4.3.2** (Helm 3 not supported) | mise | chart |
| kubectl | 1.37.2 | mise | Kubernetes |
| kind | 0.33.0 | mise | local cluster |
| helm-docs | 1.14.2 | mise | chart README |
| hadolint | 2.15.1 | mise | Dockerfile lint |
| shellcheck | 0.11.0 | mise | shell lint |
| yamllint | 1.38.0 | mise | YAML lint |
| trivy | 0.76.2 | mise | image vulnerability scan |

Versions are pinned in [mise.toml](mise.toml) and Python libraries in [uv.lock](uv.lock).

## 1. Install Docker (manually)

Docker is intentionally **not** installed by the Makefile.

* Ubuntu: https://docs.docker.com/engine/install/ubuntu/ then
  `sudo usermod -aG docker $USER` and log in again.
* macOS: https://docs.docker.com/desktop/setup/install/mac-install/ (choose Intel or Apple Silicon).

## 2. Install everything else

```bash
make install-deps
```

What it does:

```mermaid
flowchart TD
    A[make install-deps] --> B{OS?}
    B -- Ubuntu --> C[apt: curl git make jq sqlite3 ...]
    B -- macOS --> D[Xcode CLT check<br/>brew: jq sqlite - if Homebrew exists]
    C & D --> E{mise installed?}
    E -- no --> F[curl https://mise.run | sh]
    E -- yes --> G[mise install<br/>tools from mise.toml]
    F --> G
    G --> H[uv sync --frozen<br/>.venv with Python deps]
```

On Ubuntu it uses `sudo` for `apt-get`. Unsupported platforms get a warning but the script
still tries.

## 3. Activate mise in your shell (optional)

The Makefile calls tools through `mise exec`, so this is only needed to call `helm`, `uv`,
etc. directly:

```bash
echo 'eval "$(~/.local/bin/mise activate bash)"' >> ~/.bashrc   # bash
echo 'eval "$(~/.local/bin/mise activate zsh)"'  >> ~/.zshrc    # zsh
```

## 4. Check

```bash
make check-deps
```

It reports the platform, Docker, mise, every pinned tool, that Helm is v4 and that `.venv` is ready,
and exits with code 1 when something is missing.

## Manual installation

```bash
curl https://mise.run | sh
~/.local/bin/mise trust && ~/.local/bin/mise install
~/.local/bin/mise exec -- uv sync --frozen
```
