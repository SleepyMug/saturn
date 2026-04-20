# 0005 — Lifecycle commands do not seed content

> `project new` creates labelled volumes and chowns them; it does not write files. Template scaffolding is a `runtime` command run from inside a bootstrap shell.

## Context

A natural-feeling `project new <name>` would create volumes *and* drop a `.saturn/Containerfile` template into the fresh ws volume. But users may then `git clone` an existing project into that same ws volume — the cloned tree has its own `.saturn/Containerfile` and the auto-seeded one collides (either it's clobbered, or the clone fails because the dir isn't empty).

## Decision

Split structural setup from content scaffolding:

- **Structural** (engine-level): `project new` creates labelled volumes, chowns them to `agent`. No file writes.
- **Content** (filesystem-level): `saturn runtime init`, run from inside a container (typically a `project shell`), writes the `.saturn/Containerfile` template into the ws volume. Refuses to overwrite.

`project shell <name>` exists exactly to provide access to the empty/in-progress volume before any project image exists — it's a base-image shell with the ws volume mounted. Users `git clone <url> .` or `saturn runtime init` from there, then `exit` and `saturn up <name>`.

## Consequences

- Two distinct bootstrap paths, both supported:
  - **Scaffold fresh**: `project new` → `project shell` → `runtime init` → edit → `up`.
  - **Import existing repo** (via clone-inside): `project new` → `project shell` → `git clone <url> .` → `up`.
  - **Import existing repo** (via host directory): `project new` → `saturn put <host-src>/. .` → `up`. See README Option C.
- `project new` is cheap and composable — it can be followed by arbitrary content imports (put, git clone, etc.) without precondition conflicts.
- `runtime init` is explicit: users invoke it when they want a template, not accidentally when they didn't.

## Consequences for future features

Any future "create X" command must separate structure-level setup from file-level content. Engine-level side effects (volumes, labels, networks, images) stay in the lifecycle command; file-level side effects go in `runtime` commands.
