# Project model

> Derives every engine resource name from a single `<name>`. Projects exist iff their ws volume exists; discovery is label-based.

## Overview

A "project" in saturn is a tuple of engine-managed resources whose names are mechanically derived from one string. There is no project file, no config registry, no `.saturn/` directory on the host — `project_list()` interrogates the engine's volume index directly.

## Provided APIs

### `class Project(name: str)`

Constructor validates the name (non-empty, no `/`, no leading `.`, no spaces) and exposes:

| Attribute | Value | Used as |
|---|---|---|
| `name` | the input | identity |
| `container` | `saturn_<name>` | running project container |
| `bootstrap_container` | `saturn_bootstrap_<name>` | transient base-image shell for pre-image access |
| `image` | `localhost/saturn-<name>:latest` | project image (built from volume) |
| `vol_ws` | `saturn_ws_<name>` | workspace named volume |
| `ws_mount` | `/home/agent/<name>` | mount path inside the container |

All derivation is pure string formatting; there is no persistence of these mappings anywhere outside the naming convention.

### `ws_mount_for(name: str) -> str`

Returns `/home/agent/<name>`. Called from runtime commands inside the container, which only know the name (from `SATURN_PROJECT` env) and must reconstruct the mount path.

### `project_list() -> list[str]`

Runs `docker volume ls --filter label=saturn.volume=ws --format '{{.Name}}'`, strips the `saturn_ws_` prefix, returns sorted names. Filtering on the `ws`-marker label guarantees one hit per project with no dedupe logic needed.

### `project_exists(name: str) -> bool`

`docker volume inspect saturn_ws_<name>` — true iff the call succeeds.

### `ensure_volume(vol_name, project, marker) -> None`

Idempotent create-with-labels: if the volume already exists, returns. Otherwise:

1. `docker volume create --label saturn.project=<project> --label saturn.volume=<marker> <vol_name>`.
2. `docker run --rm --init --user 0 -v <vol_name>:/mnt saturn-base chown 10001:10001 /mnt` — fresh volumes are owned by root at the storage level; the chown shifts ownership so the non-root `agent` user in the image can write.

## Consumed APIs

- [`engine`, `engine_ok`, `engine_quiet`, `engine_out`](../engine/index.md#provided-apis) — all engine calls.
- [`BASE_IMAGE`](../base-image/index.md#provided-apis) — transient container image for the chown step.

## Workflows

### Discovery (`project ls`)

1. `engine_out("volume", "ls", "--filter", "label=saturn.volume=ws", "--format", "{{.Name}}")`.
2. Strip `saturn_ws_` prefix.
3. Sort and print.

Filtering on the `ws`-marker label (rather than just `saturn.project`) gives one entry per project even if later versions add additional labelled volumes per project.

### Creation (`project new <name>`)

1. `ensure_base()` — saturn-base must exist before we can run the chown container.
2. `ensure_volume(saturn_ws_<name>, <name>, "ws")`.
3. Print next-step hint.

Deliberately does **not** write any files into the volume. Users may clone an existing repo into the fresh volume; scaffolding a template would collide with cloned content. See [decisions/0005-lifecycle-vs-content.md](../../decisions/0005-lifecycle-vs-content.md).

### Removal (`project rm <name>`)

1. Confirm by typing the project name.
2. `docker rm -f saturn_<name>` (project container, if any).
3. `docker rm -f saturn_bootstrap_<name>` (in case a bootstrap shell was abandoned).
4. `docker volume rm saturn_ws_<name>`.
5. `docker rmi localhost/saturn-<name>:latest`.

All four `engine_ok` (non-checking) since any subset may already be absent.

## Execution-context constraints

- Name validation is structural only (no filesystem characters that break paths). It does not check against existing engine-reserved names or docker image-name rules beyond "no uppercase" (docker requires lowercase for image refs) — image names use the lowercased? No, we use the name as-is, so a name like `MyProj` would produce `localhost/saturn-MyProj:latest` which docker rejects. **Known limitation**: names should be lowercase + `[a-z0-9_-]`.
