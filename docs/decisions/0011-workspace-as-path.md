# 0011 — Drop project management; saturn operates on arbitrary directories

> Revises [0010](0010-workspace-no-path-symmetry.md). The notion of a "project" as a named entry under `$HOME/saturn/` is gone. A workspace is now any directory with a `.saturn/` marker. `saturn ls` and `saturn rm` are removed; `new` / `up` take an optional target directory; `down` / `shell` / `exec` act on cwd. Nested `up` is well-defined via `SATURN_WORKSPACE` (container-side) + `SATURN_HOST_WORKSPACE` (host-side), both now consumed by `_resolve_target`.
>
> **Superseded by [0012](0012-compose-native-wrapper.md).** The imperative `docker run` surface (`cmd_up`/`cmd_down`/`cmd_shell`/`cmd_exec` + `_env_flags`/`_base_mount_flags`/`_mixin_mount_flags`) is gone; saturn is a pass-through to `docker compose`. `SATURN_HOST_WORKSPACE` / `SATURN_WORKSPACE` / `SATURN_HOST_SOCK` / `SATURN_HOST_HOME` / `SATURN_MIXIN_*` all removed — reverse mount lookup via `docker inspect <self>` does their job generically. Mixins become `saturn new --<flag>` template fragments.

## Context

Across 0007–0010 the "project" model kept accruing coupling:

- A fixed host root at `$HOME/saturn/` that had to be either mounted symmetrically (0007) or navigated around (0010).
- A `Project` class with a `host_dir` that was only valid in one of host/guest mode at a time.
- An audit after 0010 found that `cmd_up` inside a container always failed at `p.host_dir.is_dir()`, `cmd_new` / `cmd_rm` silently wrote to the container overlay, and `SATURN_HOST_WORKSPACE` was plumbed but never consumed (same-project nested `up` worked only by coincidence of path derivation).

The cleaner model that emerged: saturn should not care where a workspace lives on disk. Any directory with `.saturn/` is a workspace. Container / image names come from the directory's basename. Inside a container, `saturn up <sub>` is legal as long as `<sub>` is under the current container's workspace — saturn derives the host path by appending the relative path to `SATURN_HOST_WORKSPACE`.

## Decision

- **`Workspace` replaces `Project`.** Fields: `host_path`, `view_path`, `name` (basename), `container` (`saturn_<name>`), `image` (`localhost/saturn-<name>:latest`), `container_dir` (`/root/<name>`). `view_path` is the path visible to the current saturn process (for filesystem checks); `host_path` is the daemon-resolvable host path (for `-v` sources and `docker build` args).
- **`_resolve_target(arg)` is the single entry point.** Resolves cwd (or the target arg) to a `Workspace`:
  - Host mode: `host_path == view_path == resolve(arg or '.')`.
  - Guest mode: requires `SATURN_HOST_WORKSPACE` and `SATURN_WORKSPACE`. Target must be under `SATURN_WORKSPACE`; `host_path = SATURN_HOST_WORKSPACE / target.relative_to(SATURN_WORKSPACE)`. Targets outside exit fail-fast.
- **New env var `SATURN_WORKSPACE`** — the container-side path of the current workspace. Pairs with `SATURN_HOST_WORKSPACE` (introduced in 0010) to form the translation basis. Both are now *consumed* by `_resolve_target`, not merely propagated.
- **Commands.** Drop `ls` and `rm`. `new [dir]` / `up [dir]` take an optional positional target (cwd default). `down` / `shell` / `exec` take no positional dir — they derive from cwd. `exec <cmd...>` retains the argv-intercept so user flags survive.
- **`cmd_up` reorder.** Socket check → already-running short-circuit → mixin resolve + auto-create → `ensure_base` → workspace image build → run. Missing-engine no longer leaves mixin scaffolding; already-up no longer rebuilds.
- **`engine_quiet` is gone.** `cmd_up`'s `docker run` uses `engine(...)` so docker's stderr surfaces on failure.
- **Label change.** `saturn.project=<name>` → `saturn.workspace=<host_path>`. Informational only now that discovery is gone.

## Consequences

- **Workspaces are self-contained.** A directory can be placed anywhere on disk and still be a saturn workspace. No `$HOME/saturn/` convention required. Makes integration with existing project trees (git repos, monorepos, scratch dirs) frictionless.
- **Nested `up` is deterministic and verified fail-fast.** Inside a container, `saturn up /some/other/path` exits with a clear message. `saturn up ./sub` (anything under the current workspace) works by pure relative-path arithmetic — no guessing.
- **No cross-workspace surface from inside.** Inside a container you can only launch children *within* the current workspace tree. That's a deliberate narrowing — cross-workspace orchestration goes through the host.
- **Basename collisions are user-visible.** `saturn_<basename>` is the container name. Two workspaces with the same basename collide at `up` time with docker's "name in use" error. Rename a directory to resolve.
- **`saturn ls` is gone.** For a global view, use `docker ps --filter label=saturn.workspace`. The label holds the host path so you can see where each running container's workspace lives.
- **`saturn rm` is gone.** Clean up with `saturn down` (from the workspace) + `docker rmi localhost/saturn-<name>:latest` if desired. Host-side `rm -rf <workspace>` is the user's responsibility — saturn doesn't own the directory.

## Rejected alternatives

- **Keep `Project` but make `host_dir` / `container_dir` mode-dependent.** Considered in the post-0010 audit. Rejected: reusing one field for two meanings makes every caller branch on `IS_HOST`. The `Workspace(host_path, view_path)` split is cleaner and localizes the branching in `_resolve_target`.
- **Register workspaces in a global index file.** Would restore `ls`. Rejected: the index becomes its own state to keep in sync with the filesystem; the engine-label query + the user's shell history already suffice.
- **Keep `new` creating a dir under `$HOME/saturn/` when given a bare name.** Marginal convenience, heavy coupling cost. Rejected: `saturn new ~/code/foo` and `mkdir ~/code/foo && (cd ~/code/foo && saturn new)` are both two short commands, and dropping the special case removes the last trace of the projects root.
- **Let nested `up` reach sibling workspaces by adding a `SATURN_HOST_PROJECTS_ROOT` env var.** Noted as a possibility in 0010. Rejected here: the new model has no projects root at all, so there's nothing to point such a var at. Cross-workspace orchestration happens on the host.
