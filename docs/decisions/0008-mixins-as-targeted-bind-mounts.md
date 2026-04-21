# 0008 — Mixins as targeted host bind-mounts, not named volumes

> Partially revises [0007](0007-drop-agent-user-use-is-sandbox.md). The `agent` user and sudo stay gone, path symmetry stays, but the whole-`$HOME` bind-mount is replaced by selective mixin paths + an always-mounted projects root. Supersedes [0006](0006-user-state-mixins.md)'s volume-based mixins with bind-mount mixins.

## Context

[0007](0007-drop-agent-user-use-is-sandbox.md) collapsed the mixin system into "bind-mount all of `$HOME`" — arguing that rootless userns made the ownership problem disappear, so there was no reason to enumerate credential paths. In practice the blast radius felt too large: every project container could read/write every file under the user's home, which is more than SSH keys and auth tokens.

At the same time, dropping the mixin machinery lost two useful affordances: (a) installing per-tool dependencies (`gh`, `nodejs+npm+claude-code`, `openssh-client`, etc.) at the base-image layer rather than per project, and (b) naming a user-global state bundle with a short identifier (`--mixins ssh,gh`) instead of enumerating paths.

The right shape is: keep mixins as a named registry, but have each mixin declare **host paths to bind-mount** (path-symmetric, under `$HOME`) rather than named volumes, and pair it with an optional base-image install snippet.

## Decision

- **Mixin schema**: `{ "paths": ["<home-rel>", ...], "setup": "<shell snippet>" }`. Paths are HOME-relative strings (e.g. `.ssh`, `.claude.json`, `.config/gh`) that resolve to `$HOST_HOME/<rel>`. Multiple paths per mixin (e.g. `claude` bundles `.claude` and `.claude.json`).
- **Mount model on `saturn up <name>`**:
  - Always: `-v $SATURN_ROOT:$SATURN_ROOT` (projects root, where `$SATURN_ROOT = $HOST_HOME/saturn`) and `-v $HOST_SOCK:/var/run/docker.sock`.
  - Per selected mixin path: `-v $HOST_HOME/<rel>:$HOST_HOME/<rel>` (path-symmetric).
  - Nothing else from `$HOME` is exposed automatically.
- **Setup scripts** install tool dependencies in the base image at `base default`/`template` time. Spliced as `RUN <setup>` between the base-packages layer and the `COPY saturn` step.
- **Existence check**: `saturn up --mixins ...` runs `_check_mixin_paths` before any engine call. If any selected mixin's path is missing on host, exit 1 with a named list. Prevents docker from silently creating empty directories at those paths (which then break tools expecting their config).
- **Defaults**: `ssh,claude,codex,gh`. `--mixins ''` opts out.
- **Nesting**: inside saturn, `_check_mixin_paths` checks paths against the container's view. That means a mixin is usable from nested saturn only if the outer container mounted the same mixin. Documented in [components/mixins](../components/mixins/index.md#workflows).

## Consequences

- Blast radius is bounded: the container sees the projects tree + a short list of credential paths, not the whole `$HOME`. Users can reason about what a given project container can read.
- Mixin names return as CLI sugar (`--mixins ssh,gh` vs enumerating paths).
- The base image is fatter when default mixins are used (nodejs+npm for claude+codex, openssh-client, gh). Opt out with `saturn base default --mixins ''` if undesired.
- Schema simpler than the [0006](0006-user-state-mixins.md) original: no `subpath` / `volume-subpath` hack (file-target mixins just bind-mount a file directly), no `ensure_volume` / `ensure_mixin_volume` / chown dance (bind-mounts don't need ownership prep), no `_check_mount_overlap` complexity (host paths collide predictably and docker errors name the conflicting paths clearly enough).
- Fail-fast on missing paths is a behavior change vs 0007's "silently include or exclude"; surfaces setup issues (no SSH key yet, no `gh auth login` yet) at the right moment rather than letting tools inside hit cryptic ENOENT later.

## Rejected alternatives

- **Whole-`$HOME` bind-mount** (as in 0007). Simpler code but broader blast radius; chosen against for this revision.
- **Silent skip of missing paths** at `up` time. Considered; rejected because it hides setup errors. Users who want optional-mount semantics can remove the mixin from `--mixins`.
- **Absolute paths with `~` expansion** (`"~/.ssh"`). Considered; rejected in favor of plain HOME-relative strings (`".ssh"`) — less parsing, clearer that the root is always `$HOST_HOME`.
- **Mount only the current project's dir** (`$HOST_HOME/saturn/<name>`), not the whole projects root. Tighter isolation but breaks `saturn new/ls/rm` from inside a container. Chose the more functional option since the socket itself already implies full engine trust.
