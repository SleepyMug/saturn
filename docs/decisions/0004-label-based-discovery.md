# 0004 — Discover projects via a `saturn.volume=ws` label filter

> `project ls` runs `docker volume ls --filter label=saturn.volume=ws` and strips the `saturn_ws_` prefix. One hit per project, no dedupe needed.

## Context

With no host-side state (see [0002](0002-volume-first-zero-host-state.md)), project enumeration has to interrogate the engine. Candidates were:

1. Name-prefix scan — `docker volume ls` and filter for names starting with `saturn_ws_` client-side.
2. Single label filter — both volumes get `saturn.project=<name>`; dedupe client-side.
3. Role-marker label — only the ws volume is tagged `saturn.volume=ws`; filter on that and get one hit per project.

## Decision

Two labels on the ws volume at creation:

- `saturn.project=<name>` — for filtering by project.
- `saturn.volume=ws` — role marker; exactly one volume per project carries this.

`project ls` filters on `saturn.volume=ws` (option 3). No dedupe code, one round trip to the engine, and the label scheme is future-compatible — adding other volumes per project (e.g. cache, data) doesn't contaminate the project list.

## Consequences

- `project_list()` is a 3-line function: `engine_out(...)`, split, strip `saturn_ws_` prefix, sorted.
- Third-party volumes named `saturn_ws_*` can't pollute the list unless they also carry our label — a nice property.
- Adding future per-project volumes with distinct role markers (e.g. `saturn.volume=cache`) requires only tagging them correctly; `project ls` doesn't need to change.

## Rejected alternatives

- **Name-prefix scan** — sensitive to naming collisions with user-created volumes; no clean path to add new per-project volume roles without disambiguating names.
- **Single `saturn.project` label filter** — correct but requires client-side dedupe, which is more code than the label scheme it's trying to replace.
