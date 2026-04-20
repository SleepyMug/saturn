# Mixins

> Named bundles of (install snippet + user-global volume + target path) that carry per-user state like SSH keys, gh tokens, or Claude auth into project containers.

## Overview

A mixin is a small static record declaring how a piece of user-global state (`~/.ssh`, `~/.claude.json`, `~/.codex`, ...) should be:

- **installed** (optional `RUN <command>` spliced into the base Containerfile), and
- **mounted** (a user-global named volume mapped to a target path inside the container).

One volume per mixin (`saturn_mixin_<name>`, hyphens in the name become underscores). The volume is label-discoverable (`saturn.volume=mixin`, `saturn.mixin=<name>`) and lives outside any project's lifecycle — creating or removing a project never touches mixin volumes.

Three entry points use mixins. All accept an optional `--mixins <csv>` flag; when omitted they fall back to `DEFAULT_MIXINS` (currently `ssh,claude,claude-json,codex,gh`). Pass `--mixins ''` to explicitly opt out of defaults.

- `base template --mixins <list>` / `base default --mixins <list>` — splice install commands into the rendered Containerfile (base-image composition).
- `up <name> --mixins <list>` — mount the selected mixin volumes into the project container.
- `project config [--mixins <list>]` — start a base-image shell with **only** mixin volumes mounted, for interactive setup (`ssh-keygen`, `gh auth login`, ...).

The first-use auto-build path (`ensure_base` → `_build_base(BASE_CONTAINERFILE_TEXT)`) also uses `DEFAULT_MIXINS`, so a bare `saturn up <name>` produces a container whose base image already has the default mixins' tools installed — consistent with the default mount set.

`base build <file>` does **not** splice mixin commands into user-supplied Containerfiles — those files are used verbatim. To combine a custom base with mixins, do `base template --mixins ... > my.Containerfile`, edit, `base build my.Containerfile`.

## Provided APIs

### `MIXINS: dict[str, dict]`

Inlined registry. Each entry is keyed by mixin name (e.g. `ssh`, `gh`, `claude`, `claude-json`, `codex`, `emacs`, `xdg-config`). Value schema:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `target` | `str` | yes | Absolute path inside the container where the volume is mounted. A directory for most mixins; a file path when `subpath` is set. |
| `command` | `str` | yes | Shell string spliced into the base Containerfile as `RUN <command>`. Empty means "no install step" (state-only mixins). |
| `subpath` | `str` | optional | Present iff `target` is a file. The engine mounts `<volume>/<subpath>` at `target` using `--mount ...,volume-subpath=<subpath>`. |

### `_render_base_containerfile(mixin_names: list[str]) -> str`

Returns the full base Containerfile text with mixin install lines spliced between the base-packages block and the `COPY saturn` step. With no mixins, equivalent to the original default.

Layout:

```
<BASE_CONTAINERFILE_HEAD>
USER 0
RUN <command-of-mixin-1>    # only if non-empty
RUN <command-of-mixin-2>
USER agent:agent
<BASE_CONTAINERFILE_TAIL>
```

### `DEFAULT_MIXINS: list[str]`

The set of mixin names used by every mixin-aware command when `--mixins` is not passed. Currently `["ssh", "claude", "claude-json", "codex", "gh"]` — the tools almost every session uses. Explicit `--mixins ''` (empty string) opts out of defaults.

### `_parse_mixin_list(spec: str | None) -> list[str]`

Splits a comma-separated CLI spec into validated mixin names. Empty/None → `[]`. Unknown names cause `sys.exit` with a clear error listing known names.

### `_cli_mixins(raw: str | None) -> list[str]`

Resolves the `--mixins` argument from argparse. `raw is None` (flag not passed) → `list(DEFAULT_MIXINS)`. Any explicit value (including `--mixins ''`) is parsed verbatim — the empty string is the way to opt out of defaults.

### `ensure_mixin_volume(name: str) -> None`

Idempotent create + chown for `saturn_mixin_<name>`. For subpath (file-target) mixins, additionally `touch`es the subpath file inside the volume so `volume-subpath=<subpath>` mounts succeed on first use (the engine requires the subpath to exist).

Labels applied at creation: `saturn.volume=mixin`, `saturn.mixin=<name>`.

### `_mixin_mount_args(mixin_names: list[str]) -> list[str]`

Returns the `docker run` args (`-v ...` or `--mount type=volume,...,volume-subpath=...`) for mounting each selected mixin volume at its target. Side effect: calls `ensure_mixin_volume` per name before returning.

### `_mixin_vol(name: str) -> str`

Returns `saturn_mixin_<sanitized>` where `sanitized` replaces hyphens with underscores (Docker volume names allow `[A-Za-z0-9_.-]`; we standardize on `_` inside the volume name).

### `_planned_mixin_mounts(mixin_names: list[str]) -> list[tuple[str, str]]`

Returns `(label, target)` pairs for the supplied mixins — consumed by `_check_mount_overlap` alongside the socket and ws mount entries.

### `_check_mount_overlap(mounts: list[tuple[str, str]]) -> None`

Fail-fast validation of a planned mount set before the `docker run` is issued.

- **Error (`sys.exit`)** on any two targets that resolve to the same path (same string after trailing-slash normalization). The engine would otherwise reject with a generic `Duplicate mount point:` message; this check names the conflicting labels.
- **Advisory `note:`** (stderr, non-fatal) when one target is a path-component prefix of another. Both docker and podman sort mounts shortest-prefix-first and apply the inner mount on top of the outer's corresponding subpath — which is usually the intended behavior. Verified empirically; see [experiment_journal/mount-ordering-nested-vs-duplicate-targets.md](../../experiment_journal/mount-ordering-nested-vs-duplicate-targets.md).

Callers (`cmd_up`, `cmd_project_config`) include every target planned for the upcoming `docker run`: the socket, the ws mount when applicable, and each mixin's target. This catches e.g. a hypothetical mixin colliding with `/var/run/docker.sock`.

### `_is_path_parent_of(parent: str, child: str) -> bool`

String-prefix checks are insufficient (`/foo` would match `/foobar`); `_is_path_parent_of` requires the next character after the parent to be `/`, so path components align.

### `BASE_CONTAINERFILE_TEXT: str`

Backward-compatible alias for `_render_base_containerfile([])`. Used by `ensure_base()` on auto-build.

## Consumed APIs

- [`engine_ok`, `engine_quiet`](../engine/index.md#provided-apis) — volume inspect/create + `run --rm` helpers for chown and subpath-touch.
- [`BASE_IMAGE`](../base-image/index.md#provided-apis) — image for the setup container that chowns the fresh volume.

## Workflows

### `base default --mixins ssh,gh`

1. `_parse_mixin_list("ssh,gh")` → `["ssh", "gh"]`.
2. `engine_ok("rmi", BASE_IMAGE)` (idempotent).
3. `_render_base_containerfile(["ssh", "gh"])` → text with two `RUN` lines between `USER 0` / `USER agent:agent`.
4. `_build_base(<text>)` — normal temp-context build + `COPY saturn` step (see [base-image](../base-image/index.md#workflows)).

No mixin volumes are created at this step — base image build does not mount state.

### `up <name> --mixins ssh,claude-json`

Extends the normal `up` flow. After building the project image and computing the container's `docker run` flags:

1. `_mixin_mount_args(["ssh", "claude-json"])` is called.
2. For each mixin: `ensure_mixin_volume` creates `saturn_mixin_ssh` / `saturn_mixin_claude_json` if absent, chowns to agent, and (for `claude-json`) pre-touches `claude.json` inside the volume.
3. Returned flags: `-v saturn_mixin_ssh:/home/agent/.ssh` and `--mount type=volume,source=saturn_mixin_claude_json,target=/home/agent/.claude.json,volume-subpath=claude.json`.
4. Flags are spliced into the `docker run -d ...` for the project container alongside the usual ws mount, socket mount, env flags.

### `project config --mixins ssh`

`cmd_project_config` starts a transient base-image shell with only the requested mixin volumes mounted (plus the engine socket — so the user can `docker pull` auxiliary tools if needed). No ws volume, no `SATURN_PROJECT` env — this shell is not scoped to a project.

Defaults to all mixin names when `--mixins` absent. Uses the same `_mixin_mount_args` as `up`, so mount paths are identical inside both shells.

## Execution-context constraints

- **Engine version**: `volume-subpath` requires Docker 25.0+ or Podman 4.7+. Older engines fail `up --mixins <file-target-mixin>` with an error. Directory-target mixins work on all engines that support named volumes.
- **Nested mixin targets are safe** on docker 25+ and podman 4.7+. Both engines reorder mounts shortest-prefix-first, so e.g. `xdg-config` (`/home/agent/.config`) plus `gh` (`/home/agent/.config/gh`) produces the intuitive layout: the `gh` volume provides the `gh` subdirectory, the `xdg-config` volume provides everything else. `_check_mount_overlap` prints an advisory `note:` so the interaction is visible. Empirically verified — see [experiment_journal/mount-ordering-nested-vs-duplicate-targets.md](../../experiment_journal/mount-ordering-nested-vs-duplicate-targets.md).
- **Exact-target collisions fail fast** via `_check_mount_overlap` before `docker run` is invoked. The check covers socket, ws, and every mixin target in one pass, so e.g. a mixin accidentally pointed at `/var/run/docker.sock` errors with a named-label message rather than a generic engine error.
- **Mixin registry is inlined**: adding or removing a mixin means editing `saturn` itself. Aligns with the single-file distribution principle (see [decisions/0001-single-file-distribution.md](../../decisions/0001-single-file-distribution.md)).
