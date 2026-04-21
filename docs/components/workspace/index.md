# Workspace model

> A workspace is any directory with a `.saturn/` marker. There is no global registry. `saturn up [dir]` bind-mounts the directory at `/root/<basename>` inside a container; other lifecycle commands act on the workspace for cwd.

## Overview

The previous "project" model required directories under `$HOME/saturn/<name>` and maintained a global list via `saturn ls` / `saturn rm`. That coupling is gone. A workspace is now entirely identified by its absolute host path; the container and image names are derived from its basename.

Two paths matter per workspace:

- **`host_path`** — host-absolute path. Every `-v <src>:<dst>` source and every `docker build` context/file argument uses this (the daemon resolves on the host).
- **`view_path`** — the path visible to the current saturn process. Equal to `host_path` on the host; a container-side path inside a saturn container. Filesystem checks (`is_dir`, `is_file`, `mkdir`) must use this.

## Provided APIs

### `class Workspace(host_path: Path, view_path: Path)`

Validates the basename (non-empty, no `/`, no leading `.`, no spaces) and exposes:

| Attribute | Value | Used as |
|---|---|---|
| `host_path` | input | bind-mount source; `docker build` args |
| `view_path` | input | filesystem checks by this saturn process |
| `name` | `host_path.name` | identity |
| `container` | `saturn_<name>` | container name |
| `image` | `localhost/saturn-<name>:latest` | image name |
| `container_dir` | `/root/<name>` | bind-mount target inside; cwd |
| `containerfile_view()` | `view_path / ".saturn" / "Containerfile"` | existence check |
| `containerfile_host()` | `host_path / ".saturn" / "Containerfile"` | `docker build -f` arg |

Basename collisions: two workspaces with the same basename map to the same container/image names and cannot run concurrently. Surface at `saturn up` time via docker's "container name in use" error.

### `CONTAINERFILE_TEMPLATE: str`

Seed content for a freshly-created `.saturn/Containerfile`: `FROM localhost/saturn-base:latest` plus a commented `RUN apt-get …` example.

### `_resolve_target(arg: str | None) -> Workspace`

Turns a CLI target (or cwd, when `arg is None`) into a `Workspace`.

- Resolves `arg` (or `.`) to an absolute path; exits if the path isn't a directory.
- **Host mode** (`IS_HOST`): `host_path == view_path == resolved`.
- **Guest mode**: requires `SATURN_HOST_WORKSPACE` and `SATURN_WORKSPACE`. Exits if either is unset. The resolved path must be under `SATURN_WORKSPACE` (the current container's workspace). The host path is computed as `SATURN_HOST_WORKSPACE / rel` where `rel = resolved.relative_to(SATURN_WORKSPACE)`. Targets outside the current workspace exit with a labelled message.

This is the single point where workspace env vars are consumed — they are load-bearing, not just propagated.

## Consumed APIs

- `IS_HOST`, `HOST_WORKSPACE`, `WORKSPACE` from module-level env-derived constants ([engine](../engine/index.md#provided-apis)).
- None within the codebase from other modules.

## Workflows

### Creation (`saturn new [dir]`)

1. Resolve `dir` (cwd default); `mkdir -p dir` if absent.
2. Build a `Workspace` via `_resolve_target`.
3. `mkdir -p <view_path>/.saturn` and seed `Containerfile` if absent.

Nested `saturn new <subdir>` inside a container works because the subdir is under the mounted current workspace: `mkdir` reflects to host.

### Launch (`saturn up [dir]`)

See [components/cli](../cli/index.md) for the full step sequence. The workspace is constructed once and threaded through the mount/env/build calls.

### `down` / `shell` / `exec`

All resolve the workspace from cwd. If the container isn't running, `shell` and `exec` exit with a hint; `down` just removes the container idempotently.

## Execution-context constraints

- **Basename must be a valid container/image name component.** Docker image refs require lowercase; a workspace at `/…/MyDir` produces `localhost/saturn-MyDir:latest` which docker rejects. Stick to lowercase + `[a-z0-9_-]`.
- **Nested target is bounded by the current workspace.** In guest mode, `up` and `new` only work for paths under `SATURN_WORKSPACE`. Targets outside exit with a clear message.
- **No global discovery.** `saturn ls` and `saturn rm` are gone — the workspace concept doesn't have a registry. Use `docker ps --filter label=saturn.workspace` if you need to see running containers; remove via `saturn down` (in the workspace) or `docker rm -f saturn_<name>`.
