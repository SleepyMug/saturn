# Base image

> Single-file distribution: the saturn-base Containerfile is inlined as a Python string; the build context is assembled in a temp dir with a copy of saturn itself.

## Overview

The saturn-base image (`localhost/saturn-base:latest` by default) is the parent image for every project image. Its contract: Debian-trixie-slim base, `agent` user (uid 10001), docker-cli + sudo + python3 installed, `/usr/local/bin/saturn` present and executable, sudo-NOPASSWD for agent, USER agent, WORKDIR `/home/agent`, CMD `sleep infinity`.

The build is self-contained — saturn's on-disk companions (standalone Containerfile, app.py, etc.) are gone. `saturn base` writes the inlined Containerfile text into a `TemporaryDirectory` and `shutil.copy`s the running saturn script alongside it, then invokes `docker build` against that temp context. Distribution is one file: `curl .../saturn -o ~/.local/bin/saturn && chmod +x`.

## Provided APIs

### `BASE_IMAGE: str`

`os.environ.get("SATURN_BASE_IMAGE", "localhost/saturn-base:latest")`. Used everywhere an engine call needs the base-image tag.

### `BASE_CONTAINERFILE_TEXT: str`

The inlined Containerfile. Contract:

- `FROM docker.io/library/debian:trixie-slim`
- `groupadd/useradd` for `agent` uid/gid 10001 with `/bin/bash` shell
- `apt-get install docker-cli sudo ca-certificates python3`
- `/etc/sudoers.d/agent` with `NOPASSWD: ALL`
- `COPY saturn /usr/local/bin/saturn` + `RUN chmod 0755` (portable across docker & podman classic builder; BuildKit `--chmod=` isn't available here)
- `WORKDIR /home/agent`, `USER agent:agent`, `CMD ["sleep", "infinity"]`

### `_build_base() -> None`

Used by both `ensure_base()` and `cmd_base`.

1. Open a `tempfile.TemporaryDirectory(prefix="saturn-base-")` (auto-cleanup on exit).
2. Decide Containerfile source:
   - If `SATURN_BASE_CONTAINERFILE` env is set, read that file (error if missing) and write its text into `<tmp>/Containerfile`.
   - Else, write `BASE_CONTAINERFILE_TEXT` into `<tmp>/Containerfile`.
3. `shutil.copy(SCRIPT, <tmp>/saturn)` — always, regardless of override. Overrides must keep `COPY saturn /usr/local/bin/saturn` or nesting breaks.
4. `docker build -f <tmp>/Containerfile -t BASE_IMAGE <tmp>`.

`SCRIPT = Path(__file__).resolve()` at module top — so the copy works from any CWD.

### `ensure_base() -> None`

`docker image inspect BASE_IMAGE`; if present, no-op. Else prints "building base image (first time only)" and calls `_build_base()`. Used as a prerequisite in almost every top-level command.

### `cmd_base(_args) -> None`

Explicit rebuild. `docker rmi BASE_IMAGE` (ignored if absent), then `_build_base()`.

## Consumed APIs

- [`engine`, `engine_ok`](../engine/index.md#provided-apis).
- `SCRIPT` constant — absolute path to the running saturn script.

## Workflows

### Fresh host, first `saturn up demo`

1. `project_exists("demo")` → true (user ran `project new` earlier).
2. `ensure_base()` — `docker image inspect` fails (image doesn't exist).
3. `_build_base()`:
   a. `TemporaryDirectory("saturn-base-")` → `/tmp/saturn-base-xxxxx/`.
   b. Write `BASE_CONTAINERFILE_TEXT` to `Containerfile`.
   c. `shutil.copy("/home/user/.local/bin/saturn", "/tmp/.../saturn")`.
   d. `docker build -f /tmp/.../Containerfile -t localhost/saturn-base:latest /tmp/.../`.
4. `TemporaryDirectory` auto-removes on context exit.
5. Proceed with the rest of `up` (project image build + container start).

### Custom base for a bespoke project

1. User writes their own Containerfile at `~/my-base/Containerfile` that starts `FROM ubuntu:24.04 AS base` and still includes `COPY saturn /usr/local/bin/saturn`, etc.
2. `SATURN_BASE_CONTAINERFILE=~/my-base/Containerfile saturn base`.
3. `_build_base()` reads the user file text into the temp dir's `Containerfile`, still copies saturn into the context alongside.

## Execution-context constraints

- **Classic Docker builder only**. `DOCKER_BUILDKIT=0` is forced at module top because podman's docker-compat socket doesn't serve BuildKit.
- **COPY ordering**. The `COPY saturn` step must come after the `apt-get install` layer and before `USER agent` so the subsequent `chmod 0755` runs as root.
- **shutil.copy preserves mode**. On Linux `copy()` also copies mode bits; if the source saturn is `0755`, the dest is `0755`. The explicit `RUN chmod 0755` in the Containerfile is belt-and-suspenders for cases where the source mode is off.
