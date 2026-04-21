# CLI

> Argparse subparser tree, `main()` dispatch, and a sys.argv intercept for `exec` so user commands keep their flags.

## Overview

saturn is argparse-driven. `main()` assembles a tree of subparsers, parses `sys.argv`, and dispatches to a `cmd_*` function via `args.fn(args)`. One special case: `saturn exec <cmd...>` must preserve arbitrary flags in `<cmd...>` (e.g. `saturn exec ls -la`), so `main()` intercepts `sys.argv[1] == "exec"` before argparse runs.

## Provided APIs

### `main() -> None`

Entry point when the script is invoked directly.

- Reads `sys.argv`; if `sys.argv[1] == "exec"`, runs `cmd_exec(Namespace(cmd=sys.argv[2:]))` and returns before argparse parses anything.
- Otherwise builds the argparse tree and parses normally.
- If no `fn` attribute is set (user typed `saturn` or `saturn base` with no subcommand), prints help.
- Propagates `KeyboardInterrupt` as exit 130 and `subprocess.CalledProcessError` with the child's exit code.

### Command surface

All commands live at top level — there is no `project` or `workspace` group.

| Command | Handler | Semantics |
|---|---|---|
| `new [dir]` | `cmd_new` | `mkdir -p <dir>` (default cwd), then `mkdir -p <dir>/.saturn` and seed `.saturn/Containerfile` if absent. No engine calls. |
| `up [dir] [--mixins <csv>] [--mixin-root <dir>]` | `cmd_up` | Resolve `<dir>` (default cwd) to a `Workspace`. Check socket. If container already running → no-op. Otherwise: resolve+auto-create mixin slots, `ensure_base`, build workspace image from `<dir>/.saturn/Containerfile` (if present), tear down any stopped `saturn_<name>`, `docker run` with the workspace bind-mounted at `/root/<name>`. `--mixins` selects mixin bundles (default `DEFAULT_MIXINS`; `--mixins ''` opts out). `--mixin-root` (host mode) re-roots mixin default paths. |
| `down` | `cmd_down` | `docker rm -f saturn_<name>` where `<name>` = cwd workspace basename. Idempotent. |
| `shell` | `cmd_shell` | `docker exec -it saturn_<name> /bin/bash` for cwd's workspace; errors if container isn't running. |
| `exec <cmd...>` | `cmd_exec` | `docker exec -it saturn_<name> <cmd...>` for cwd's workspace; errors if container isn't running. |

`base` group (saturn-base image lifecycle):

| Command | Handler | Semantics |
|---|---|---|
| `base template [--mixins <csv>]` | `cmd_base_template` | Write the rendered Containerfile to stdout. Mixin setups splice as `RUN` lines. Defaults to `DEFAULT_MIXINS`. |
| `base default [--mixins <csv>]` | `cmd_base_default` | Force-rebuild saturn-base (`docker rmi` then `_build_base(_render_base_containerfile(mixins))`). Defaults to `DEFAULT_MIXINS`. |
| `base build <file>` | `cmd_base_build` | Force-rebuild saturn-base from a user-supplied Containerfile (errors if missing). **No `--mixins`** — user file is verbatim. |

## Consumed APIs

- [`Workspace`, `_resolve_target`](../workspace/index.md#provided-apis) — target → Workspace resolution; single point of consumption for `SATURN_HOST_WORKSPACE` / `SATURN_WORKSPACE`.
- [`ensure_base()`, `_build_base()`](../base-image/index.md#provided-apis) — saturn-base availability.
- [engine wrappers (`engine`, `engine_ok`, `engine_out`, `engine_exec`)](../engine/index.md#provided-apis) — subprocess to docker CLI.
- [runtime helpers (`check_socket`, `container_status`, `_interactive_flags`, `_env_flags`, `_base_mount_flags`)](../engine/index.md#provided-apis).
- [mixin helpers (`MIXINS`, `_cli_mixins`, `_render_base_containerfile`, `_resolve_mixin_slots`, `_ensure_mixin_host_paths`, `_mixin_mount_flags`)](../mixins/index.md#provided-apis).

## Workflows

### Dispatch + exec intercept

1. `main()` inspects `sys.argv[1]`. If it's `"exec"`, it constructs a Namespace by hand and calls `cmd_exec` — bypassing argparse entirely so user flags survive.
2. Otherwise argparse parses. Every subparser sets `fn` via `set_defaults(fn=...)`.
3. If `fn` is unset (empty `base` subcommand), print top-level help and exit 0.

### Interactive I/O

`_interactive_flags()` returns `["-it"]` if stdin is a TTY, else `["-i"]`. The docker CLI rejects `-t` without a TTY, so unguarded use breaks piped invocations (`saturn exec ls | head`).

### stdout line buffering

`sys.stdout.reconfigure(line_buffering=True)` at startup so saturn's own `print()` interleaves correctly with subprocess output when piped.

## Execution-context constraints

- No third-party deps. argparse + subprocess + pathlib + shutil + tempfile + os/sys only.
- `os.execvp` is used for interactive commands (`shell`, `exec`) so saturn exits and the child process takes over; errors after that point won't be caught.
- `down`, `shell`, `exec` take no positional args — they derive the workspace from cwd. To operate on a different workspace, `cd` there first.
