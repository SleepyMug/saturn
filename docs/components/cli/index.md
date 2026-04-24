# CLI

> Three saturn-specific commands (`new`, `base`, `shell`) and a pass-through for everything else. Argparse is used only where flags exist; unknown argv goes through verbatim to `docker compose`.

## Overview

`main()` reads `sys.argv[1]` and routes on three cases:

1. `new` — argparse handles `target` + boolean flags → `cmd_new`.
2. `base` — argparse handles the `default` / `build <file>` subcommands → `cmd_base_default` / `cmd_base_build`.
3. Everything else — becomes pass-through: saturn gathers `.saturn/compose.yaml` plus any override files (auto-globbed `compose.override*.yaml` and `SATURN_COMPOSE_OVERRIDES`), translates the merged spec (env substitution, reverse mount lookup if in guest mode), writes `.saturn/compose.json`, and invokes `docker compose -f compose.json -p <basename> <argv...>`.

`saturn shell` is a thin alias — it's rewritten to `exec dev bash` before pass-through dispatch.

## Provided APIs

### `main() -> None`

Entry point when invoked as `saturn`.

- Empty argv (or `-h` / `--help` / `help`) prints the module docstring and returns.
- `argv[0] == "new"` parses the rest with argparse (one optional positional, one bool per flag) and calls `cmd_new`.
- `argv[0] == "base"` dispatches to the base subparser.
- `argv[0] == "shell"` rewrites argv to `["exec", "dev", "bash"]` and falls through.
- Otherwise calls `passthrough(argv)`.
- Propagates `KeyboardInterrupt` as exit 130; `subprocess.CalledProcessError` with the child's returncode.

### Command surface

**Saturn-specific:**

| Command | Handler | Semantics |
|---|---|---|
| `new [dir] [--ssh] [--gh] [--claude] [--codex] [--nesting]` | `cmd_new` | `mkdir -p <dir>` (default cwd), then `mkdir -p <dir>/.saturn` and seed `Dockerfile` + `compose.yaml`. Host-mode auto-create for each selected flag's bind source. No engine calls. |
| `base default` | `cmd_base_default` | Force-rebuild `localhost/saturn-base:latest` from the inlined minimal Dockerfile. |
| `base build <file>` | `cmd_base_build` | Force-rebuild the base from a user-supplied Dockerfile. Error if the file is missing. |
| `shell` | alias | Rewrites argv to `exec dev bash` → pass-through. |
| `host-addr` | `cmd_host_addr` | Print `localhost` (host mode) or `host.docker.internal` (guest mode) — the address to reach the host from the current context. |

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

- [`cmd_new`, `_find_workspace`](../workspace/index.md#provided-apis) — seeding templates + finding cwd's workspace.
- [`cmd_base_default`, `cmd_base_build`, `_build_base`](../base-image/index.md#provided-apis) — base image lifecycle.
- [`_translate_compose`, `passthrough`](../engine/index.md#provided-apis) — the translate + forward pipeline.

## Workflows

### Dispatch flow

1. `main()` inspects `sys.argv[1]`.
2. For `new` and `base`, argparse parses argument-specific flags (no REMAINDER hacks — the pass-through path covers anything with a free-form argv).
3. For `shell`, argv is rewritten and falls through.
4. For `host-addr`, prints the host address and returns (no engine calls).
5. Pass-through calls `passthrough(argv)`:
   - `_find_workspace()` → walk cwd upward for `.saturn/compose.yaml`.
   - `_find_overrides(ws)` → the `.saturn/compose.override*.yaml` glob + `SATURN_COMPOSE_OVERRIDES` env var.
   - `_translate_compose([base, *overrides], project)` → `.saturn/compose.json` (the merged, translated spec).
   - `subprocess.run(["docker", "compose", "-f", compose_json, "-p", project, *argv])`.
   - On non-zero exit, print the full command that was run before propagating the exit code.

### stdout line buffering

`sys.stdout.reconfigure(line_buffering=True)` at module top so saturn's own `print()` interleaves correctly with subprocess output when piped.

## Execution-context constraints

- **No argparse REMAINDER intercept.** The old `saturn exec <cmd> [args...]` pre-argparse hack is gone — pass-through covers arbitrary argv naturally.
- **`saturn up` is foreground by default.** Compose's default is foreground; saturn doesn't rewrite to `-d`. Users who want detach must pass `-d` themselves. (This is a deliberate divergence from the pre-compose saturn, where `up` was always detached.)
- **stdlib only.** argparse, subprocess, pathlib, shutil, tempfile, os, sys, json, socket. No third-party deps.
