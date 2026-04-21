# Engine ops

> Subprocess wrappers around the `docker` CLI + socket/env setup. The only module that calls subprocess.

## Overview

Every engine mutation goes through one of five wrappers: `engine`, `engine_quiet`, `engine_ok`, `engine_out`, `engine_exec`. Each invokes `docker <args>` — no sudo, never `podman`. Socket path, `DOCKER_HOST`, and (for podman only) `DOCKER_BUILDKIT=0` are derived at module import time from a small env contract.

Choosing `docker` as the CLI (rather than branching on `podman` vs `docker`) means saturn works identically against rootless podman's docker-compat API and rootless Docker.

## Provided APIs

### Env-derived constants

| Constant | Source | Meaning |
|---|---|---|
| `ENGINE` | `SATURN_ENGINE`, default `podman` | Picks the default socket path only; CLI is always `docker`. |
| `SOCK` | `SATURN_SOCK`, default derived from `ENGINE` + `XDG_RUNTIME_DIR` | Socket this saturn invocation talks to. |
| `HOST_SOCK` | `SATURN_HOST_SOCK`, default `SOCK` | Host-side path bind-mounted into child containers at `/var/run/docker.sock`. |
| `HOST_HOME` | `SATURN_HOST_HOME` or `Path.home()` | Host-side `$HOME` bind-mounted into child containers path-symmetrically. |

At import: `os.environ["DOCKER_HOST"] = f"unix://{SOCK}"`. `DOCKER_BUILDKIT=0` is additionally set **only when `ENGINE == "podman"`** — podman's docker-compat socket doesn't serve BuildKit. Docker (including rootless) serves BuildKit; forcing it off triggers a deprecation warning and loses features.

### Subprocess wrappers

| Wrapper | Semantics |
|---|---|
| `engine(*args)` | `subprocess.run(check=True)` — raises `CalledProcessError` on non-zero. Output goes to saturn's own stdout/stderr. |
| `engine_quiet(*args)` | Same but stdout+stderr are `DEVNULL`. Used for idempotent create/rm calls we don't want to narrate. |
| `engine_ok(*args)` | Returns `True` iff the call succeeds; `DEVNULL`s output. For idempotent operations where presence/absence is the question. |
| `engine_out(*args)` | Returns captured stdout as a stripped `str`, or `None` on failure. For `inspect -f '{{...}}'` queries. |
| `engine_exec(*args)` | `os.execvp` — replaces the current process. Used for interactive `exec` so saturn steps out of the way for the user's shell. |

### Runtime helpers

These live next to the wrappers and are the primitives used by every command.

- `check_socket() -> None` — asserts `SOCK` is a socket file, else exits with a clear message.
- `container_status(name) -> str` — `engine_out("inspect", "-f", "{{.State.Status}}", name)` or empty string if the container doesn't exist.
- `_interactive_flags() -> list[str]` — `["-it"]` if stdin is a TTY, else `["-i"]`. Required because docker CLI rejects `-t` without a TTY.
- `_env_flags() -> list[str]` — the canonical block of `-e SATURN_ENGINE=... -e SATURN_SOCK=/var/run/docker.sock -e SATURN_HOST_SOCK=<host-sock> -e SATURN_HOST_HOME=<host-home> -e HOME=<host-home>` injected into every child saturn container. See [boundaries/nested-env.md](../../boundaries/nested-env.md).
- `_base_mount_flags() -> list[str]` — the canonical `-v $SATURN_ROOT:$SATURN_ROOT -v $HOST_SOCK:/var/run/docker.sock` pair applied to every saturn container. The projects root is always mounted path-symmetrically so nested saturn can read/write any project; nothing else from `$HOME` comes in automatically.
- [`_mixin_mount_flags`, `_check_mixin_paths`](../mixins/index.md#provided-apis) — `up`'s additional per-mixin bind-mounts layered on top of `_base_mount_flags()`.

## Consumed APIs

None within the codebase — this is the leaf module. Externally, consumes:

- `docker` CLI.
- Unix domain socket at `$SOCK`.

## Workflows

### Nested propagation

1. Host: `saturn up demo` runs with `SOCK=/run/user/1000/podman/podman.sock`, `HOST_SOCK=/run/user/1000/podman/podman.sock`, `HOST_HOME=/home/guest`.
2. The project container starts with `-v /home/guest/saturn:/home/guest/saturn -v /run/user/1000/podman/podman.sock:/var/run/docker.sock` (plus any `--mixins` paths, plus env propagation).
3. Inside: `saturn ls` re-reads the env, talks to `/var/run/docker.sock`, scans `/home/guest/saturn/` (bind-mounted), and prints the same list as the host.
4. Inside: `saturn up demo2` spawns a sibling on the host engine using the propagated host-side paths as bind-mount *sources* — not the inside-container ones.

Without `SATURN_HOST_SOCK` / `SATURN_HOST_HOME`, double-nesting would try to bind-mount `/var/run/docker.sock` and inside paths on the host — those don't resolve there.

## Execution-context constraints

- **Classic builder required on podman.** When `ENGINE == "podman"`, saturn forces `DOCKER_BUILDKIT=0` at module import. The env flows to any subprocess saturn spawns.
- **No direct podman calls.** Bypassing the socket opens the rootless store directly and reintroduces store-corruption races. The README's "Avoiding podman storage races" section spells this out.
- **`os.execvp` is terminal.** After `engine_exec`, saturn is replaced by the child process — trailing cleanup code won't run. Callers must complete everything else first.
