# Engine ops

> Subprocess wrappers around the `docker` CLI + socket/env setup. The only module that calls subprocess.

## Overview

Every engine mutation goes through one of five wrappers which prepend `sudo` if `SATURN_SUDO=1` is set, then always `docker <args>`. Socket path, `DOCKER_HOST`, and `DOCKER_BUILDKIT=0` are derived at module import time from a small env contract.

Choosing `docker` as the CLI (rather than branching on `podman` vs `docker`) means saturn works identically against rootless podman's docker-compat API and rootless Docker. See [decisions/0003-sudo-over-group-add.md](../../decisions/0003-sudo-over-group-add.md).

## Provided APIs

### Env-derived constants

| Constant | Source | Meaning |
|---|---|---|
| `ENGINE` | `SATURN_ENGINE`, default `podman` | Picks the default socket path only; CLI is always `docker`. |
| `SOCK` | `SATURN_SOCK`, default derived from `ENGINE` + `XDG_RUNTIME_DIR` | Socket this saturn invocation talks to. |
| `HOST_SOCK` | `SATURN_HOST_SOCK`, default `SOCK` | Host-side path that gets bind-mounted into child containers. |
| `USE_SUDO` | `SATURN_SUDO == "1"` | Prepend sudo to every engine call. |

At import: `os.environ["DOCKER_HOST"] = f"unix://{SOCK}"` and `os.environ["DOCKER_BUILDKIT"] = "0"`. These flow to both saturn's own docker calls and to subprocesses that inherit env.

### `_engine_cmd(*args: str) -> list[str]`

Returns `["sudo", "docker", *args]` if `USE_SUDO` else `["docker", *args]`. All other wrappers build on this.

### Subprocess wrappers

| Wrapper | Semantics |
|---|---|
| `engine(*args)` | `subprocess.run(check=True)` — raises `CalledProcessError` on non-zero. Output goes to saturn's own stdout/stderr. |
| `engine_quiet(*args)` | Same but stdout+stderr are `DEVNULL`. Used for idempotent create/rm calls we don't want to narrate. |
| `engine_ok(*args)` | Returns `True` iff the call succeeds; `DEVNULL`s output. For idempotent operations where presence/absence is the question. |
| `engine_out(*args)` | Returns captured stdout as a stripped `str`, or `None` on failure. For `inspect -f '{{...}}'` queries. |
| `engine_exec(*args)` | `os.execvp` — replaces the current process. Used for interactive `run`/`exec` so saturn steps out of the way for the user's shell. |

### Runtime helpers

These aren't strictly "engine ops" but they live next to the wrappers and are the primitives used by every command.

- `check_socket() -> None` — asserts `SOCK` is a socket file, else exits with a clear message. Called before any engine mutation that needs the daemon.
- `container_status(name) -> str` — `engine_out("inspect", "-f", "{{.State.Status}}", name)` or empty string if the container doesn't exist.
- `_interactive_flags() -> list[str]` — `["-it"]` if stdin is a TTY, else `["-i"]`. Required because docker CLI rejects `-t` without a TTY (podman was forgiving).
- `_project_env_flags(p) -> list[str]` — the canonical block of `-e SATURN_ENGINE=docker -e SATURN_SOCK=... -e SATURN_HOST_SOCK=... -e SATURN_SUDO=1 -e SATURN_PROJECT=<name>` that gets injected into every child saturn container. See [boundaries/nested-env.md](../../boundaries/nested-env.md).

## Consumed APIs

None within the codebase — this is the leaf module. Externally, consumes:

- `docker` CLI (classic builder path).
- `sudo` when `USE_SUDO=1` (only inside containers).
- Unix domain socket at `$SOCK`.

## Workflows

### Nested propagation

1. Host: `saturn up demo` runs with `USE_SUDO=0`, `SOCK=/run/user/1000/podman/podman.sock`, `HOST_SOCK=/run/user/1000/podman/podman.sock`.
2. The project container starts with `-e SATURN_SUDO=1 -e SATURN_SOCK=/var/run/docker.sock -e SATURN_HOST_SOCK=/run/user/1000/podman/podman.sock -v /run/user/1000/podman/podman.sock:/var/run/docker.sock` (plus `SATURN_ENGINE=docker` and `SATURN_PROJECT=demo`).
3. Inside: `saturn exec demo sh` → inside `sh`, run `saturn project ls`. saturn re-reads env, computes `USE_SUDO=True`, `SOCK=/var/run/docker.sock`, prepends sudo; listing returns the same set of projects as from the host.

The `HOST_SOCK` env var is what makes double-nesting work: when the nested saturn creates *another* child container, it bind-mounts the *original* host socket path (not `/var/run/docker.sock`, which is a container-local path and wouldn't resolve on the host).

## Execution-context constraints

- **Classic builder only.** `DOCKER_BUILDKIT=0` is set at import; the podman docker-compat socket doesn't serve BuildKit.
- **No direct podman calls.** A saturn developer tempted to `podman volume inspect` for speed would bypass the serializer (since rootless podman has no persistent daemon) and reintroduce store-corruption races. See [decisions/0003-sudo-over-group-add.md](../../decisions/0003-sudo-over-group-add.md) and the README's "Avoiding podman storage races" section.
- **`os.execvp` is terminal.** After `engine_exec`, saturn is replaced by the child process — trailing cleanup code won't run. Callers must complete everything else first.
