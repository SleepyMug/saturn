# Mixins

> Named bundles of (HOME-relative bind-mount paths + install snippet) that bring user-global state like SSH keys, gh tokens, or Claude auth into project containers without mounting the whole host `$HOME`.

## Overview

A mixin is a small static record that declares:

- **`paths`**: a list of HOME-relative strings. Each resolves to `$HOST_HOME/<rel>` and is bind-mounted path-symmetrically (`-v <host>:<host>`) on `saturn up`. Multiple paths per mixin supported — e.g. the `claude` mixin mounts both `~/.claude` (the state dir) and `~/.claude.json` (the config file).
- **`setup`**: an optional shell string spliced into the base Containerfile as `RUN <setup>` when `saturn base default` / `base template` render it. Empty means no install step.

Three entry points use mixins. All accept an optional `--mixins <csv>` flag; when omitted they fall back to `DEFAULT_MIXINS` (currently `ssh,claude,codex,gh`). Pass `--mixins ''` to opt out.

- `base template --mixins <list>` / `base default --mixins <list>` — splice setup lines into the rendered base Containerfile.
- `up <name> --mixins <list>` — bind-mount each selected mixin's paths into the project container.

`base build <file>` does **not** splice setups into user-supplied Containerfiles — those files are used verbatim. Combine a custom base with mixins via: `base template --mixins ... > my.Containerfile`, edit, `base build my.Containerfile`.

## Provided APIs

### `MIXINS: dict[str, dict]`

Inlined registry. Each entry is keyed by mixin name. Value schema:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `paths` | `list[str]` | yes | HOME-relative strings. Each bind-mounted at `$HOST_HOME/<path>` path-symmetrically on `up`. |
| `setup` | `str` | yes | Shell snippet spliced into the base Containerfile as `RUN <setup>`. Empty string = no install. |

Built-ins: `ssh`, `gh`, `claude`, `codex`.

### `DEFAULT_MIXINS: list[str]`

`["ssh", "claude", "codex", "gh"]` — the set every mixin-aware command uses when `--mixins` is omitted. Explicit `--mixins ''` opts out; `--mixins <csv>` picks a different set.

### `_parse_mixin_list(spec: str | None) -> list[str]`

Splits a CLI spec into validated mixin names. Empty/None → `[]`. Unknown names cause `sys.exit`.

### `_cli_mixins(raw: str | None) -> list[str]`

Resolves `--mixins`. `raw is None` (flag not passed) → `list(DEFAULT_MIXINS)`. Any explicit value (including `''`) is parsed verbatim.

### `_render_base_containerfile(mixin_names: list[str]) -> str`

Returns the full base Containerfile text with each mixin's non-empty `setup` spliced as `RUN <setup>` between the base-packages block and the `COPY saturn` step.

### `_mixin_paths(names: list[str]) -> list[tuple[str, str]]`

Returns `(mixin-name, absolute-host-path)` pairs for every path in every selected mixin — used by both the existence check and the mount flags so they see identical input.

### `_check_mixin_paths(names: list[str]) -> None`

Exits if any selected mixin's path does not exist on the host. Prints every missing path labelled by mixin and suggests remediation (`ssh-keygen`, `gh auth login`, ...). Called by `cmd_up` before any engine calls.

### `_mixin_mount_flags(names: list[str]) -> list[str]`

Returns the `docker run` args (`-v <host>:<host>` per path) for each selected mixin. No-side-effect (existence is handled separately by `_check_mixin_paths`).

## Consumed APIs

None directly from other saturn modules — this module is self-contained. Downstream callers use `_mixin_mount_flags` alongside `_base_mount_flags` from [engine](../engine/index.md).

## Workflows

### `base default --mixins ssh,gh`

1. `_parse_mixin_list("ssh,gh")` → `["ssh", "gh"]`.
2. `engine_ok("rmi", BASE_IMAGE)` (idempotent).
3. `_render_base_containerfile(["ssh", "gh"])` → text with two `RUN` lines between base-packages and `COPY saturn`.
4. `_build_base(<text>)` — standard temp-context build.

### `up <name> --mixins ssh,claude`

Extends the normal `up` flow:

1. `_cli_mixins(raw)` → `["ssh", "claude"]`.
2. `_check_mixin_paths([...])` — verifies `$HOST_HOME/.ssh`, `$HOST_HOME/.claude`, `$HOST_HOME/.claude.json` all exist on the host. Exits with a labelled list if any are missing.
3. Normal project-image build.
4. `_mixin_mount_flags(...)` returns three `-v` flag pairs, spliced into the `docker run` alongside `_base_mount_flags()` (which carries the projects root + socket) and `_env_flags()`.

### Nesting

Inside a saturn container, `HOST_HOME` is the host-side value (propagated via `SATURN_HOST_HOME`). Mixin paths resolve against it identically. **Caveat**: the existence check runs against the *inside* view of those paths — so to use a mixin from nested saturn, the outer container must have the same mixin mounted too. Otherwise the path doesn't exist inside and `_check_mixin_paths` fails.

## Execution-context constraints

- **Fail-fast on missing paths.** Missing `$HOST_HOME/.ssh` etc. causes `up` to exit before any engine work. Remedy: create the path (`ssh-keygen`, `gh auth login`, ...) or drop the mixin from `--mixins`.
- **Mixin registry is inlined** in `saturn` itself. Adding or removing a mixin is editing the script — aligns with single-file distribution.
- **Path symmetry is load-bearing.** Mixin paths bind to the same inside path as on host (`-v $HOST_HOME/.ssh:$HOST_HOME/.ssh`). With `HOME=$HOST_HOME` injected into the container, `~/.ssh` inside resolves to the bind-mounted host `.ssh` — tools that look up `~` find their config naturally.
