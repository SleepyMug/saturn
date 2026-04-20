# 0002 — Zero host state; projects discovered by volume labels

> All per-project content (Containerfile, source, `.git`) lives in the ws volume. No `.saturn/` on host. `project ls` queries engine volume labels.

## Context

The first iteration anchored a project to a `.saturn/` directory in CWD (or an ancestor), similar to how git walks up for `.git/`. This collided with the volume-first workflow: if source lives inside `saturn_ws_<name>`, there's no natural host-side "project root" for the anchor to sit in. `.saturn/` became half-committed-with-the-project, half-host-side-bootstrap — ambiguous in both roles.

## Decision

- Remove all host-side state. Every command that needs a project takes `<name>` positionally.
- Tag the ws volume at creation with labels:
  - `saturn.project=<name>`
  - `saturn.volume=ws`
- `project ls` runs `docker volume ls --filter label=saturn.volume=ws --format '{{.Name}}'` and strips the `saturn_ws_` prefix. Filtering on the ws-marker label gives one hit per project without dedupe.
- Move `.saturn/Containerfile` *inside* the ws volume at `<ws>/.saturn/Containerfile`, so it's committed with the project's git alongside the source.

## Consequences

- Users can `cd` anywhere on the host and run saturn commands; the name is the anchor.
- `project new` creates volumes only (no file writes); scaffolding a template Containerfile is a *runtime* op (`saturn runtime init` from inside a `project shell`). See [0005-lifecycle-vs-content.md](0005-lifecycle-vs-content.md).
- `up` must build from a volume rather than a host directory: a transient saturn-base container mounts the ws volume + host socket and runs `docker build` from inside. The image travels back out through the socket.
- Project name is always positional — rejected "ambient default via `SATURN_PROJECT` host env" as fake simplicity (user's phrase).

## Rejected alternatives

- **Keep `.saturn/` on host, source in volume** — mixes ownership of the project's Containerfile: is it a host artifact or a volume artifact? Source-of-truth ambiguity breaks `git clone` workflows.
- **Require a `.saturn/` file inside the volume as the marker**, scanning volumes for that file — requires mounting every volume briefly to discover projects. Labels are cheaper and engine-native.
