# CLI

> Argparse subparser tree, `main()` dispatch, and a sys.argv intercept for `exec` so user commands keep their flags.

## Overview

saturn is argparse-driven. `main()` assembles a tree of subparsers, parses `sys.argv`, and dispatches to a `cmd_*` function via `args.fn(args)`. One special case: `saturn exec <name> <cmd...>` must preserve arbitrary flags in `<cmd...>` (e.g. `saturn exec demo ls -la`), so `main()` intercepts `sys.argv[1] == "exec"` before argparse runs.

## Provided APIs

### `main() -> None`

Entry point when the script is invoked directly.

- Reads `sys.argv`; if `sys.argv[1] == "exec"`, runs `cmd_exec(Namespace(name=sys.argv[2], cmd=sys.argv[3:]))` and returns before argparse parses anything.
- Otherwise builds the argparse tree and parses normally.
- If no `fn` attribute is set (user typed `saturn` or `saturn project` with no subcommand), prints help.
- Propagates `KeyboardInterrupt` as exit 130 and `subprocess.CalledProcessError` with the child's exit code.

### Command surface

Top-level:

| Command | Handler | Semantics |
|---|---|---|
| `base` | `cmd_base` | Force-rebuild saturn-base (`docker rmi` then `_build_base()`). |
| `up <name>` | `cmd_up` | Build project image from volume, then create+run the project container. No-op if already running. |
| `down <name>` | `cmd_down` | `docker rm -f saturn_<name>`. Idempotent. Volumes kept. |
| `shell <name>` | `cmd_shell` | `exec -it saturn_<name> /bin/bash`; errors if container isn't running. |
| `exec <name> <cmd...>` | `cmd_exec` | `exec -it saturn_<name> <cmd...>`; errors if container isn't running. |
| `put <name> <src> [<dst>]` | `cmd_put` | Copy host path into the project volume; see [../put-get](../../architecture.md#saturn-put-name-host-src-dst--import-files). |
| `get <name> <src> [<dst>]` | `cmd_get` | Copy volume path out to host. |

`project` group (host-side lifecycle):

| Command | Handler | Semantics |
|---|---|---|
| `project ls` | `cmd_project_ls` | List projects via label filter on `saturn.volume=ws`. |
| `project new <name>` | `cmd_project_new` | Create labelled ws volume, chown to agent. No file writes. |
| `project rm <name>` | `cmd_project_rm` | Remove container, volume, and project image. Requires typing project name to confirm. |
| `project shell <name>` | `cmd_project_shell` | Base-image shell with the project's ws volume mounted, for bootstrap (clone, scaffold) before any project image exists. |

`runtime` group (in-container; requires `SATURN_PROJECT` env):

| Command | Handler | Semantics |
|---|---|---|
| `runtime info` | `cmd_runtime_info` | Print project name, ws mount, Containerfile presence. |
| `runtime init` | `cmd_runtime_init` | Scaffold `.saturn/Containerfile` template in ws; refuses to overwrite. |

## Consumed APIs

- [`Project(name)`](../project/index.md#provided-apis) — construct resource-name bundle from a single name.
- [`project_list()`, `project_exists(name)`](../project/index.md#provided-apis) — label-based discovery.
- [`ensure_base()`, `_build_base()`](../base-image/index.md#provided-apis) — saturn-base availability.
- [engine wrappers (`engine`, `engine_ok`, `engine_quiet`, `engine_out`, `engine_exec`)](../engine/index.md#provided-apis) — subprocess to docker CLI.
- [runtime helpers (`check_socket`, `ensure_volume`, `container_status`, `_interactive_flags`, `_project_env_flags`)](../engine/index.md#provided-apis).

## Workflows

### Dispatch + exec intercept

1. `main()` inspects `sys.argv[1]`. If it's `"exec"`, it constructs a Namespace by hand and calls `cmd_exec` — bypassing argparse entirely so user flags survive.
2. Otherwise argparse parses. Every subparser sets `fn` via `set_defaults(fn=...)`.
3. If `fn` is unset (empty `project` or `runtime`), print top-level help and exit 0.

### Interactive I/O

`_interactive_flags()` returns `["-it"]` if stdin is a TTY, else `["-i"]`. Used for `docker exec` and `docker run` calls that drop into interactive shells. The docker CLI (unlike older podman) rejects `-t` without a TTY — without this guard, `saturn exec demo ls | head` fails.

### stdout line buffering

`sys.stdout.reconfigure(line_buffering=True)` runs at startup so saturn's own `print()` calls interleave correctly with subprocess output when piped (e.g. `saturn up demo | tail`). Without this, Python block-buffers stdout when piped and the ordering relative to docker's writes is scrambled.

## Execution-context constraints

- No third-party deps. argparse + subprocess + pathlib + shutil + tempfile + os/sys only.
- `os.execvp` is used for interactive commands (`shell`, `exec`, `project shell`) so saturn exits and the child process takes over; errors after that point won't be caught.
