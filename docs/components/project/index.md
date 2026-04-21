# Project model

> Derives every engine resource name from a single `<name>`. Projects exist as host directories under `$HOST_HOME/saturn/`; discovery unions dir listing with container labels.

## Overview

A "project" in saturn is a tuple of (host directory, container, image) whose names are mechanically derived from one string. There is no project registry; `project_list()` interrogates the filesystem and the engine directly and unions the results.

## Provided APIs

### `class Project(name: str)`

Constructor validates the name (non-empty, no `/`, no leading `.`, no spaces) and exposes:

| Attribute | Value | Used as |
|---|---|---|
| `name` | the input | identity |
| `container` | `saturn_<name>` | running project container |
| `image` | `localhost/saturn-<name>:latest` | project image (built from the host dir) |
| `host_dir` | `$HOST_HOME/saturn/<name>` | project host directory |
| `containerfile()` | `<host_dir>/.saturn/Containerfile` | optional per-project Containerfile |

All derivation is pure string/path formatting.

### `SATURN_ROOT: Path`

`Path(HOST_HOME) / "saturn"` — where projects live. `HOST_HOME` comes from `SATURN_HOST_HOME` (when set, e.g. inside a nested saturn) or falls back to `Path.home()`.

### `CONTAINERFILE_REL: str`

`.saturn/Containerfile` — relative to the project dir. Build context is the project dir itself.

### `CONTAINERFILE_TEMPLATE: str`

Seed content written by `saturn new`: `FROM localhost/saturn-base:latest` plus a commented `RUN apt-get …` example. Runs as root (no USER directive).

### `project_list() -> list[str]`

Unions two sources:

1. Directory children of `$SATURN_ROOT` that look like projects — not hidden, and containing a `.saturn/` subdirectory (the marker created by `saturn new`). This filter keeps unrelated subdirs of `$HOME/saturn/` out of the listing.
2. Containers carrying a `saturn.project` label — picked up via `docker ps -a --filter label=saturn.project --format '{{index .Labels "saturn.project"}}'`.

The union surfaces projects that exist only as a host dir (never started) and projects that exist only as a container (host dir was removed out-of-band).

## Consumed APIs

- [`engine_out`](../engine/index.md#provided-apis) — for the container-label listing.

## Workflows

### Discovery (`saturn ls`)

1. List dirs under `$HOST_HOME/saturn/`.
2. Ask the engine for container labels.
3. Sort the union and print.

### Creation (`saturn new <name>`)

1. `host_dir.mkdir(parents=True, exist_ok=True)`.
2. If `.saturn/Containerfile` doesn't exist, write the seed template.
3. Print the path and suggested next command.

No engine calls — the project exists as soon as the directory exists.

### Removal (`saturn rm <name>`)

1. Confirm by typing the project name (skipped with `-f`).
2. `docker rm -f saturn_<name>` (best-effort).
3. `docker rmi localhost/saturn-<name>:latest` (best-effort).
4. `shutil.rmtree(host_dir)`.

## Execution-context constraints

- Name validation is structural (no filesystem-breaking chars). Docker image refs require lowercase; names like `MyProj` produce `localhost/saturn-MyProj:latest` which docker rejects. **Known limitation**: stick to lowercase + `[a-z0-9_-]`.
- Running as container-root under rootless userns means `saturn new` inside a container creates the dir with host-user ownership on disk. Same for `rm`'s `rmtree`.
