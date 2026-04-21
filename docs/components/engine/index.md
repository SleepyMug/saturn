# Engine ops

> Subprocess wrappers around the `docker` CLI + socket/env setup. The only module that calls subprocess.

## Overview

Every engine mutation goes through one of four wrappers: `engine`, `engine_ok`, `engine_out`, `engine_exec`. Each invokes `docker <args>` — no sudo, never `podman`. Socket path, `DOCKER_HOST`, and (for podman only) `DOCKER_BUILDKIT=0` are derived at module import time from a small env contract.

Choosing `docker` as the CLI (rather than branching on `podman` vs `docker`) means saturn works identically against rootless podman's docker-compat API and rootless Docker.

## Provided APIs

### Env-derived constants

| Constant | Source | Meaning |
|---|---|---|
| `ENGINE` | `SATURN_ENGINE`, default `podman` | Picks the default socket path only; CLI is always `docker`. |
| `SOCK` | `SATURN_SOCK`, default derived from `ENGINE` + `XDG_RUNTIME_DIR` | Socket this saturn invocation talks to. |
| `HOST_SOCK` | `SATURN_HOST_SOCK`, default `SOCK` | Host-side path bind-mounted into child containers at `/var/run/docker.sock`. |
| `HOST_HOME` | `SATURN_HOST_HOME` or `Path.home()` | Host-side `$HOME`. Used as the prefix for each mixin slot's host-mode default fallback. |
| `IS_HOST` | `os.environ.get("SATURN_IN_GUEST") != "1"` | True when this saturn invocation is on the host; False inside a saturn container. Drives host-mode auto-create of mixin paths and host-mode env-var fallbacks. |
| `HOST_WORKSPACE` | `SATURN_HOST_WORKSPACE` or `None` | Host-side absolute path of the current container's workspace. Consumed by `_resolve_target` in guest mode. |
| `WORKSPACE` | `SATURN_WORKSPACE` or `None` | Container-side absolute path of the current container's workspace (e.g. `/root/<name>`). Consumed by `_resolve_target` in guest mode to compute `rel = target.relative_to(WORKSPACE)`. |

At import: `os.environ["DOCKER_HOST"] = f"unix://{SOCK}"`. `DOCKER_BUILDKIT=0` is additionally set **only when `ENGINE == "podman"`** — podman's docker-compat socket doesn't serve BuildKit. Docker (including rootless) serves BuildKit; forcing it off triggers a deprecation warning and loses features.

### Subprocess wrappers

| Wrapper | Semantics |
|---|---|
| `engine(*args)` | `subprocess.run(check=True)` — raises `CalledProcessError` on non-zero. Output goes to saturn's own stdout/stderr. |
| `engine_ok(*args)` | Returns `True` iff the call succeeds; `DEVNULL`s output. For idempotent operations where presence/absence is the question. |
| `engine_out(*args)` | Returns captured stdout as a stripped `str`, or `None` on failure. For `inspect -f '{{...}}'` queries. |
| `engine_exec(*args)` | `os.execvp` — replaces the current process. Used for interactive `exec` so saturn steps out of the way for the user's shell. |

### Runtime helpers

These live next to the wrappers and are the primitives used by every command.

- `check_socket() -> None` — asserts `SOCK` is a socket file, else exits with a clear message.
- `container_status(name) -> str` — `engine_out("inspect", "-f", "{{.State.Status}}", name)` or empty string if the container doesn't exist.
- `_interactive_flags() -> list[str]` — `["-it"]` if stdin is a TTY, else `["-i"]`. Required because docker CLI rejects `-t` without a TTY.
- `_env_flags(slots: list[dict], workspace: Workspace) -> list[str]` — the canonical block of `-e SATURN_ENGINE=... -e SATURN_SOCK=/var/run/docker.sock -e SATURN_HOST_SOCK=<host-sock> -e SATURN_HOST_HOME=<host-home> -e SATURN_IN_GUEST=1 -e SATURN_HOST_WORKSPACE=<workspace.host_path> -e SATURN_WORKSPACE=<workspace.container_dir>` plus one `-e <slot.env>=<slot.host_path>` per resolved mixin slot. No `HOME` injection — container `HOME` stays at the image default (`/root`). See [boundaries/nested-env.md](../../boundaries/nested-env.md).
- `_base_mount_flags() -> list[str]` — just the engine-socket bind (`-v $HOST_SOCK:/var/run/docker.sock`). The per-workspace mount (`-v <workspace.host_path>:/root/<name>`) and any mixin mounts are added by `cmd_up` on top of this base.
- [`_resolve_mixin_slots`, `_ensure_mixin_host_paths`, `_mixin_mount_flags`](../mixins/index.md#provided-apis) — `up`'s per-slot host-path resolution, host-mode auto-create, and per-slot `-v <host>:<target>` bind-mounts layered on top of `_base_mount_flags()`.
- [`Workspace`, `_resolve_target`](../workspace/index.md#provided-apis) — the target-dir → Workspace resolution the engine helpers receive input from.

## Consumed APIs

None within the codebase — this is the leaf module. Externally, consumes:

- `docker` CLI.
- Unix domain socket at `$SOCK`.

## Workflows

### Nested propagation

1. Host: `saturn up /tmp/demo` runs with `SOCK=/run/user/1000/podman/podman.sock`, `HOST_SOCK=/run/user/1000/podman/podman.sock`, `HOST_HOME=/home/guest`, `IS_HOST=True`. `_resolve_target('/tmp/demo')` builds `Workspace(host_path=/tmp/demo, view_path=/tmp/demo, name='demo')`.
2. For each selected mixin slot, the outer saturn resolves `host_path` (env var or `$HOST_HOME/<default_host>`) and auto-creates anything missing.
3. The container starts with `-v /run/user/1000/podman/podman.sock:/var/run/docker.sock -v /tmp/demo:/root/demo -v <host_path>:<target>` (one `-v` per slot) and `-e SATURN_ENGINE=... -e SATURN_SOCK=... -e SATURN_HOST_SOCK=... -e SATURN_HOST_HOME=... -e SATURN_HOST_WORKSPACE=/tmp/demo -e SATURN_WORKSPACE=/root/demo -e SATURN_IN_GUEST=1 -e <slot.env>=<host_path>` (one `-e` per slot). cwd is `/root/demo`.
4. Inside (`IS_HOST=False`): user `cd`s into `/root/demo/sub`, runs `saturn up`. `_resolve_target(None)` resolves cwd to `/root/demo/sub`, sees it's under `WORKSPACE=/root/demo`, computes `rel=sub` and `host_path=/tmp/demo/sub`. Builds `Workspace(host_path=/tmp/demo/sub, view_path=/root/demo/sub, name='sub')`.
5. Sibling container `saturn_sub` starts on the host engine with `-v /tmp/demo/sub:/root/sub` and propagated per-slot mixin vars — no defaults, no creation (guest mode).

Without `SATURN_HOST_SOCK` / `SATURN_HOST_HOME`, the socket mount can't be reconstructed and mixin defaults have no base. Without `SATURN_HOST_WORKSPACE` / `SATURN_WORKSPACE`, inner saturn can't derive the host path for any target. Without the per-slot `SATURN_MIXIN_*` vars, nested mixin mounts fail fast in guest mode (labelled error pointing at the outer invocation).

## Execution-context constraints

- **Classic builder required on podman.** When `ENGINE == "podman"`, saturn forces `DOCKER_BUILDKIT=0` at module import. The env flows to any subprocess saturn spawns.
- **No direct podman calls.** Bypassing the socket opens the rootless store directly and reintroduces store-corruption races. The README's "Avoiding podman storage races" section spells this out.
- **`os.execvp` is terminal.** After `engine_exec`, saturn is replaced by the child process — trailing cleanup code won't run. Callers must complete everything else first.
