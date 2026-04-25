# CLI

> Saturn-specific commands (`new`, `base`, `shell`, `host-addr`), a direct-docker shim (`docker`), and a compose pass-through for everything else. Argparse is used only where flags exist; the `docker` shim and the unknown-argv compose path forward verbatim.

## Overview

`main()` (in `src/saturn/cli.py`) calls `env.probe_engine()` once for non-help invocations, then reads `sys.argv[1]` and routes:

1. `new` — argparse handles `target` + boolean flags → `cmd_new`.
2. `base` — argparse handles the `default` / `build <file>` subcommands → `cmd_base_default` / `cmd_base_build`.
3. `docker` — `cmd_docker(argv[1:])`: forwards the remaining argv verbatim to the `docker` CLI on `$PATH` and exits with its returncode. No argparse, no compose, no translation.
4. `shell` — argv is rewritten to `["exec", "dev", "bash"]` and falls into the compose pass-through.
5. `host-addr` — `cmd_host_addr` prints `localhost` or `host.docker.internal` and returns.
6. Everything else — compose pass-through: saturn gathers `.saturn/compose.yaml` plus any override files (auto-globbed `compose.override*.yaml` and `SATURN_COMPOSE_OVERRIDES`), translates the merged spec (env substitution, reverse mount lookup if in guest mode), writes `.saturn/compose.json`, and invokes `docker compose -f compose.json -p <basename> <argv...>`.

## Provided APIs

### `main() -> None`

Entry point when invoked as `saturn` (zipapp), `python -m saturn`, or any wrapper.

- Empty argv (or `-h` / `--help` / `help`) prints the help block and returns *without* running the engine probe.
- For every other invocation, `env.probe_engine()` runs first — adapts `DOCKER_BUILDKIT` and fail-fasts on a podman-CLI × non-podman-backend mismatch.
- `argv[0] == "new"` parses the rest with argparse (one optional positional, one bool per flag) and calls `cmd_new`.
- `argv[0] == "base"` dispatches to the base subparser.
- `argv[0] == "docker"` calls `cmd_docker(argv[1:])` — direct CLI shim, no compose.
- `argv[0] == "shell"` rewrites argv to `["exec", "dev", "bash"]` and falls through to the compose pass-through.
- `argv[0] == "host-addr"` calls `cmd_host_addr()` and returns.
- Otherwise calls `passthrough(argv)`.
- The zipapp's `__main__.py` propagates `KeyboardInterrupt` as exit 130 and `subprocess.CalledProcessError` with the child's returncode.

### Command surface

**Saturn-specific:**

| Command | Handler | Semantics |
|---|---|---|
| `new [dir] [--ssh] [--gh] [--claude] [--codex] [--nesting]` | `cmd_new` | `mkdir -p <dir>` (default cwd), then `mkdir -p <dir>/.saturn` and seed `Dockerfile` + `compose.yaml`. Host-mode auto-create for each selected flag's bind source. No engine calls. |
| `base default` | `cmd_base_default` | Force-rebuild `localhost/saturn-base:latest` from the inlined minimal Dockerfile. |
| `base build <file>` | `cmd_base_build` | Force-rebuild the base from a user-supplied Dockerfile. Error if the file is missing. |
| `shell` | alias | Rewrites argv to `exec dev bash` → pass-through. |
| `host-addr` | `cmd_host_addr` | Print `localhost` (host mode) or `host.docker.internal` (guest mode) — the address to reach the host from the current context. |
| `docker <args>` | `cmd_docker` | Forward `<args>` verbatim to the `docker` CLI on `$PATH` (with saturn's `DOCKER_HOST`/`DOCKER_BUILDKIT` already set). Skips compose translation entirely; lets callers run `docker exec saturn_foo bash`, `docker logs saturn_bar -f`, `docker images`, etc. against the same engine compose hits. Exits 2 with a usage line if no `<args>` are given. |

**Pass-through** (anything not listed above):

| Example | Becomes |
|---|---|
| `saturn up -d` | `docker compose -f .saturn/compose.json -p <ws> up -d` |
| `saturn up` | `docker compose -f .saturn/compose.json -p <ws> up` *(foreground, not `-d`)* |
| `saturn down` | `docker compose -f .saturn/compose.json -p <ws> down` |
| `saturn exec dev bash` | `docker compose -f .saturn/compose.json -p <ws> exec dev bash` |
| `saturn logs -f` | `docker compose -f .saturn/compose.json -p <ws> logs -f` |
| `saturn ps` | `docker compose -f .saturn/compose.json -p <ws> ps` |

The workspace is discovered by walking cwd upward for `.saturn/compose.yaml`; the project name (`-p`) is the workspace basename. For commands that shouldn't be tied to a workspace, `cd` outside any `.saturn`-bearing tree — saturn exits with a clear "no `.saturn/compose.yaml`" message.

## Consumed APIs

- [`cmd_new`, `find_workspace`](../workspace/index.md#provided-apis) — seeding templates + finding cwd's workspace.
- [`cmd_base_default`, `cmd_base_build`, `_build_base`](../base-image/index.md#provided-apis) — base image lifecycle.
- [`_translate_compose`, `passthrough`, `cmd_host_addr`](../engine/index.md#provided-apis) — the translate + forward pipeline.
- `cmd_docker` (`saturn/docker.py`) — direct docker CLI pass-through.
- `env.probe_engine` — adaptive `DOCKER_BUILDKIT` + cli/backend mismatch fail-fast.

## Workflows

### Dispatch flow

1. `main()` checks for help-style argv first (`-h` / `--help` / `help` / empty) — prints help and returns without probing.
2. `env.probe_engine()` runs once: detects cli (docker vs podman shim) + backend (Podman Engine in `docker version` stdout?), adapts `DOCKER_BUILDKIT`, hard-fails on a podman-CLI-against-non-podman-engine mismatch.
3. For `new` and `base`, argparse parses argument-specific flags (no REMAINDER hacks — the pass-through paths cover anything with a free-form argv).
4. For `docker`, calls `cmd_docker(argv[1:])` — `subprocess.run(["docker", *argv])` and exit with its returncode. No translation, no compose, no `_run` wrapper (returncode propagation needs to be flat).
5. For `shell`, argv is rewritten to `exec dev bash` and falls into the compose pass-through.
6. For `host-addr`, prints the host address and returns (no engine calls).
7. Compose pass-through calls `passthrough(argv)`:
   - `find_workspace()` → walk cwd upward for `.saturn/compose.yaml`.
   - `_find_overrides(ws)` → the `.saturn/compose.override*.yaml` glob + `SATURN_COMPOSE_OVERRIDES` env var.
   - `_translate_compose([base, *overrides], project)` → `.saturn/compose.json` (the merged, translated spec).
   - `subprocess.run(["docker", "compose", "-f", compose_json, "-p", project, *argv])`.
   - On non-zero exit, print the full command that was run before propagating the exit code.

### stdout line buffering

`sys.stdout.reconfigure(line_buffering=True)` at the top of `main()` so saturn's own `print()` interleaves correctly with subprocess output when piped.

## Execution-context constraints

- **No argparse REMAINDER intercept.** The old `saturn exec <cmd> [args...]` pre-argparse hack is gone — pass-through covers arbitrary argv naturally.
- **`saturn up` is foreground by default.** Compose's default is foreground; saturn doesn't rewrite to `-d`. Users who want detach must pass `-d` themselves. (This is a deliberate divergence from the pre-compose saturn, where `up` was always detached.)
- **`saturn docker` is argparse-free.** Every flag is forwarded — `saturn docker --help` runs `docker --help`, not saturn's. Compose-shaped operations should still go through the compose pass-through (so the translation pipeline + project naming applies); use `saturn docker` only when you need to address the engine directly (running `docker exec`, `docker inspect`, etc. on saturn-managed objects, or invoking custom docker subcommands the compose pass-through can't reach).
- **stdlib only.** argparse, subprocess, pathlib, shutil, tempfile, os, sys, json, socket, zipapp. No third-party deps.
