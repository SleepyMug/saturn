# Mixins

> Named bundles of (slot records + install snippet) that bring user-global state like SSH keys, gh tokens, or Claude auth into workspace containers at fixed container targets — without mounting the whole host `$HOME`. Each slot's host-side source is supplied via a dedicated env var (with a host-mode default fallback).

## Overview

A mixin is a small static record that declares:

- **`slots`**: a list of per-path records. Each slot has:
  - `env` — name of the env var that carries the host-side source path.
  - `target` — fixed container-side path the slot is bind-mounted at (e.g. `/root/.ssh`). Container tools expect to find the data here.
  - `kind` — `"file"` or `"dir"`. Used by host-mode auto-create.
  - `default_host` — HOME-relative fallback host path, applied only in host mode when `env` is unset.
- **`setup`**: an optional shell string spliced into the base Containerfile as `RUN <setup>` when `saturn base default` / `base template` render it. Empty means no install step.

Three entry points use mixins. All accept an optional `--mixins <csv>` flag; when omitted they fall back to `DEFAULT_MIXINS` (currently `ssh,claude,codex,gh`). Pass `--mixins ''` to opt out.

- `base template --mixins <list>` / `base default --mixins <list>` — splice setup lines into the rendered base Containerfile.
- `up [dir] --mixins <list> [--mixin-root <dir>]` — bind-mount each selected mixin's slots into the workspace container at their fixed targets. `--mixin-root` (host mode only) re-roots all default host paths under a single parent dir instead of `$HOST_HOME` — useful for scratch or shared profiles. Per-slot `SATURN_MIXIN_*` env vars still override the default.

`base build <file>` does **not** splice setups into user-supplied Containerfiles — those files are used verbatim. Combine a custom base with mixins via: `base template --mixins ... > my.Containerfile`, edit, `base build my.Containerfile`.

### Host mode vs. guest mode

`IS_HOST = os.environ.get("SATURN_IN_GUEST") != "1"` distinguishes the two:

- **Host mode** (outer `saturn up` from the user's shell): missing slot env vars fall back to `$HOST_HOME/<default_host>`; missing host paths are auto-created (file or dir per `kind`).
- **Guest mode** (inside a saturn container, `SATURN_IN_GUEST=1`): slot env vars are required — the outer saturn already resolved and propagated them. Missing env var → exit. No defaults, no filesystem creation (inner saturn cannot fix host state).

## Provided APIs

### `MIXINS: dict[str, dict]`

Inlined registry. Each entry is keyed by mixin name. Value schema:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `slots` | `list[dict]` | yes | Per-path records (see below). Mounted on `up`. |
| `setup` | `str` | yes | Shell snippet spliced into the base Containerfile as `RUN <setup>`. Empty string = no install. |

Slot record schema:

| Key | Type | Meaning |
|---|---|---|
| `env` | `str` | Env var holding the host-side source path. |
| `target` | `str` | Fixed container-side target for the bind-mount. |
| `kind` | `"file"` \| `"dir"` | What to auto-create on host if missing. |
| `default_host` | `str` | HOME-relative host-mode fallback when `env` is unset. |

Built-ins: `ssh`, `gh`, `claude`, `codex`.

### `DEFAULT_MIXINS: list[str]`

`["ssh", "claude", "codex", "gh"]` — the set every mixin-aware command uses when `--mixins` is omitted. Explicit `--mixins ''` opts out; `--mixins <csv>` picks a different set.

### `_parse_mixin_list(spec: str | None) -> list[str]`

Splits a CLI spec into validated mixin names. Empty/None → `[]`. Unknown names cause `sys.exit`.

### `_cli_mixins(raw: str | None) -> list[str]`

Resolves `--mixins`. `raw is None` (flag not passed) → `list(DEFAULT_MIXINS)`. Any explicit value (including `''`) is parsed verbatim.

### `_render_base_containerfile(mixin_names: list[str]) -> str`

Returns the full base Containerfile text with each mixin's non-empty `setup` spliced as `RUN <setup>` between the base-packages block and the `COPY saturn` step.

### `_resolve_mixin_slots(names: list[str], mixin_root: str | None = None) -> list[dict]`

Returns one resolved record per slot across the selected mixins: `{mixin, env, target, kind, host_path}`. `host_path` is derived from `os.environ[env]` (required in guest mode) or `<root>/<default_host>` (host-mode fallback), where `<root>` is `mixin_root` if given and `HOST_HOME` otherwise. `mixin_root` is ignored in guest mode. Exits with a labelled list of missing env vars in guest mode.

### `_ensure_mixin_host_paths(slots: list[dict]) -> None`

Host-mode only. For each slot whose `host_path` does not exist on disk, creates it per `kind` (`mkdir -p` for dirs, parent-mkdir + `touch` for files) and prints a `created <kind>: <path>  (mixin: <name>)` line so the side effect is visible.

### `_mixin_mount_flags(slots: list[dict]) -> list[str]`

Returns the `docker run` args (`-v <host_path>:<target>` per slot) for each resolved slot. No path symmetry — the target is always the slot's fixed container path.

## Consumed APIs

None directly from other saturn modules — this module is self-contained. Downstream callers (`cmd_up`) use `_resolve_mixin_slots` + `_ensure_mixin_host_paths` + `_mixin_mount_flags` alongside `_base_mount_flags` / `_env_flags` from [engine](../engine/index.md). `_env_flags(slots)` consumes each slot's `env`/`host_path` to propagate the same var into the child container for nested saturn.

## Workflows

### `base default --mixins ssh,gh`

1. `_parse_mixin_list("ssh,gh")` → `["ssh", "gh"]`.
2. `engine_ok("rmi", BASE_IMAGE)` (idempotent).
3. `_render_base_containerfile(["ssh", "gh"])` → text with two `RUN` lines between base-packages and `COPY saturn`.
4. `_build_base(<text>)` — standard temp-context build.

### `up <name> --mixins ssh,claude` (host mode)

Extends the normal `up` flow:

1. `_cli_mixins(raw)` → `["ssh", "claude"]`.
2. `_resolve_mixin_slots([...], mixin_root=getattr(args, "mixin_root", None))` → three slot records: `ssh` → `(SATURN_MIXIN_SSH, /root/.ssh, dir, $HOST_HOME/.ssh)`, `claude` → `(SATURN_MIXIN_CLAUDE, /root/.claude, dir, ...)` and `(SATURN_MIXIN_CLAUDE_JSON, /root/.claude.json, file, ...)`. Any env var explicitly set on the host overrides the default; `--mixin-root <dir>` re-roots the default under `<dir>` instead of `$HOST_HOME`.
3. `_ensure_mixin_host_paths(slots)` — creates any missing host paths (auto-create is the behavior change from prior designs).
4. Normal workspace-image build.
5. `_mixin_mount_flags(slots)` returns three `-v <host>:<target>` pairs, spliced into the `docker run` alongside `_base_mount_flags()` (socket only), the workspace mount (`-v <ws.host_path>:/root/<name>`), and `_env_flags(slots, ws)` (which carries `SATURN_IN_GUEST=1`, `SATURN_HOST_WORKSPACE`, `SATURN_WORKSPACE`, plus each slot's host path in its env var).

### Nesting

Inside a saturn container (`SATURN_IN_GUEST=1`), `_resolve_mixin_slots` reads each slot's env var directly — the outer saturn propagated those. No host-side defaults apply; no creation. If a user inside requests `--mixins ssh` but the outer didn't include `ssh`, `SATURN_MIXIN_SSH` is unset and saturn exits with a clear "env var unset inside container" message pointing at the outer invocation.

### Explicit isolation

Two user-facing knobs for diverging from the host's own config:

1. **Per-slot env var** — override a single slot. Example: give a container a scratch `~/.claude.json` distinct from the host's:

   ```
   SATURN_MIXIN_CLAUDE_JSON=/tmp/myws/.sandbox/claude.json saturn up /tmp/myws
   ```

2. **`--mixin-root <dir>`** — re-root *all* mixin default paths under a single parent directory (host mode only). Example: use a workspace-local mixin profile for everything:

   ```
   saturn up /tmp/myws --mixin-root /tmp/myws/.sandbox-home
   ```

   Each slot's default path becomes `/tmp/myws/.sandbox-home/<default_host>` (e.g. `.../.ssh`, `.../.claude.json`). Individual `SATURN_MIXIN_*` env vars still override the re-rooted default.

In both cases, host-mode auto-create ensures the path exists (empty file or empty directory, per the slot's `kind`) before the bind-mount; the container writes to it without touching the user's real `$HOME/...`.

## Execution-context constraints

- **Host-mode auto-create is side-effecting.** Running `saturn up` as a user on the host may create directories and empty files under `$HOME` (or any path the user pointed an env var at). Printed on creation.
- **Guest mode is strict.** Inside a container, every requested mixin's slot env vars must be set by the outer saturn. No fallbacks, no filesystem writes.
- **Mixin registry is inlined** in `saturn` itself. Adding or removing a mixin (or a slot) is editing the script — aligns with single-file distribution.
- **Container target is fixed per slot.** Mixin targets under `/root/` align with the container's default `HOME=/root`, so `~/.ssh`, `~/.claude.json`, etc. resolve naturally inside without injecting a custom `HOME`.
