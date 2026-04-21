# 0010 — Drop workspace path symmetry; mount current project at `/root/<name>`

> Revises [0009](0009-explicit-host-path-env-vars.md), which had kept projects-root symmetry as explicitly out of scope. The whole-projects-root mount is now gone too; only the current project is bind-mounted, at a fixed container path. The mixin design from 0009 is unchanged.
>
> **Superseded by [0011](0011-workspace-as-path.md).** The `Project` model, the `$HOME/saturn/` root, and the `ls`/`rm` surface are all gone. A workspace is now any directory with a `.saturn/` marker; `SATURN_HOST_WORKSPACE` (introduced here) is now actually consumed inside a container (alongside a new `SATURN_WORKSPACE` container-side var) to translate targets into host paths.

## Context

[0009](0009-explicit-host-path-env-vars.md) made each mixin's container target fixed and independent of the host source, but kept the projects-root mount path-symmetric (`$HOST_HOME/saturn:$HOST_HOME/saturn`). That meant the container still leaked a host path into its filesystem: inside a saturn container, the user's cwd was something like `/home/guest/saturn/foo`, which embeds the host username. It also gave every project container read/write access to every other project under `$HOST_HOME/saturn/` — broader than necessary when the user is working on one project.

The natural shape, mirroring the mixin change, is to treat the workspace like any other slot: a host source, a fixed container target, and a dedicated env var carrying the host source so nested saturn can reconstruct it.

## Decision

- **`Project.container_dir = Path("/root") / name`.** Every project container mounts `<host_dir>:<container_dir>` (`-v $HOST_HOME/saturn/<name>:/root/<name>`) and sets cwd to `/root/<name>`. The projects-root mount is gone.
- **New env var `SATURN_HOST_WORKSPACE`** carries the host-side path of the current project. Outer saturn injects it; inner saturn reads it to re-bind-mount when re-launching the same project as a sibling.
- **`_base_mount_flags()`** is now just the engine socket. The per-project workspace mount is added explicitly in `cmd_up`.
- **`_env_flags(slots, workspace_host_path=None)`** emits `-e SATURN_HOST_WORKSPACE=<path>` when given, alongside the previously-added `SATURN_IN_GUEST=1` and per-slot `SATURN_MIXIN_*` vars.
- **Container `HOME` continues to default to `/root`.** The project at `/root/<name>` and the mixin slots at `/root/.<tool>` sit side-by-side under HOME, so `~/<project>` and `~/.ssh` resolve naturally without any HOME injection.

## Consequences

- **Inside view is host-agnostic.** `/root/myproj` inside no longer leaks the host username or `$HOME` layout. Tooling in screenshots, logs, and editor config references portable paths.
- **Tighter blast radius.** A container can only read/write its own project's host dir, not siblings. Combined with 0009's per-slot mixin paths, the full surface is: socket + one project dir + each selected mixin slot.
- **Nested saturn is narrower.** Same-project `saturn up <current>` still works (it re-reads `SATURN_HOST_WORKSPACE`). `saturn new <X>` and `saturn rm <X>` from inside a container **don't work** — the host projects root is not mounted, so the filesystem write would land in a non-existent inside path. `saturn ls` from inside falls back to the engine-label scan (misses projects that exist only as a host dir). This is a regression vs 0008 ("the more functional option since the socket itself already implies full engine trust"), traded for the tighter isolation and portable paths above.
- **Discovery is split by mode.** `project_list()` on host does the dir scan + label union as before. Inside a container, `SATURN_ROOT` still resolves (via `SATURN_HOST_HOME`) to a host-side path that isn't filesystem-accessible — the dir scan silently returns nothing, and only the label scan contributes.

## Rejected alternatives

- **Mount `$HOST_HOME/saturn` at `/root` itself** so every project appears at `/root/<name>` and siblings stay accessible. Tempting — keeps nested saturn fully functional. Rejected because it replaces container `HOME` contents with the projects root, which conflicts semantically with mixin slots also living under `/root/` (mixin targets would nest into the projects-root mount). The blast-radius win of isolating siblings also evaporates.
- **Two mounts: projects root AND `/root/<name>`** for the current project. Keeps nested saturn working and gives a clean primary path, but makes the same project visible at two different inside paths — a class of confusion that's easy to create and hard to reason about.
- **Add a sibling `SATURN_HOST_PROJECTS_ROOT` env var and let inner saturn talk to the host engine about sibling projects without mounting them.** Possible future direction: inner `saturn up <other>` resolves the host-side path from the env var and tells the daemon to bind-mount it (even though inside saturn can't read the contents). Deferred — not needed for the stated goal, and adds complexity to `Project` discovery/creation paths.
