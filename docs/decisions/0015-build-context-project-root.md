# 0015 — Seeded build context is the project root; Dockerfile path is `.saturn/Dockerfile`

> Revises the `_COMPOSE_HEAD` template from [0012](0012-compose-native-wrapper.md).
> `saturn new` now seeds `build.context: ..` + `build.dockerfile:
> .saturn/Dockerfile` instead of `context: .` + `dockerfile: Dockerfile`.
> Existing workspaces are not auto-migrated; the change is transparent
> unless a Dockerfile uses `COPY` / `ADD`.

## Context

Compose resolves `build.context` relative to the compose file's directory.
`.saturn/compose.yaml` lives in `.saturn/`, so `context: .` pointed at
`.saturn/` itself. That happened to work with the shipped flag-template
Dockerfiles (`FROM base` + `RUN apt-get install …`) because none of them
`COPY` from the build context — the context was never actually read.

The latent bug surfaces the moment anyone edits the seeded Dockerfile to
add a `COPY`. `COPY pyproject.toml /tmp/` from a project-root `pyproject.toml`
fails with a confusing "file not found" because the context is `.saturn/`,
not the project. Every user who hits this has to re-derive the same
workaround (bump context up, prefix dockerfile path).

Devcontainer solves this at the seed template: `.devcontainer/` holds the
Dockerfile, but the build context is the project root. Saturn's conventions
— `.saturn/` subdir holds both Dockerfile and compose.yaml, workspace root
is one level up — make the same choice natural.

## Decision

- **`_COMPOSE_HEAD` in `saturn new`'s seed template**:

  ```yaml
  build:
    context: ..
    dockerfile: .saturn/Dockerfile
  ```

  Replaces the prior `context: .` + `dockerfile: Dockerfile`.

- **No migration for existing workspaces.** The committed `.saturn/compose.yaml`
  in a workspace that already has one is user-owned; saturn doesn't
  rewrite it. Dockerfiles seeded by prior saturn versions don't `COPY`,
  so they keep working with the old shape. A user who edits in a `COPY`
  and hits the context issue updates the two lines by hand.

- **No change to anything else.** Compose-native pass-through,
  reverse-mount lookup, guest-mode pre-build — all untouched. The
  guest-mode pre-build step (`docker build -f <ctx>/<dockerfile>`)
  already took the context and dockerfile from compose's resolved spec,
  so it follows the new shape automatically.

## Consequences

- **`COPY` / `ADD` against project files now works out of the box.** The
  common case — "add a file I maintain in the project" — is a one-line
  `COPY` without editing the seed template.

- **First rebuild after upgrade invalidates the layer cache.** Docker's
  cache key includes the build context contents; the project root has
  a strictly larger fileset than `.saturn/` alone. First `saturn up -d`
  on an upgraded workspace rebuilds from scratch. After that, normal
  caching resumes.

- **Build-context transfer is slightly larger.** Previously saturn
  streamed only `.saturn/` to the daemon on build; now it streams the
  project root. Mitigated by `.dockerignore` — users with big trees
  (`.git/`, `node_modules/`, build outputs) should add a `.dockerignore`
  at the project root, same as any other project-rooted Dockerfile
  setup. The seed doesn't auto-create one (non-obvious what to
  exclude per-project).

- **Slight blast-radius expansion for untrusted Dockerfiles.** A
  malicious `COPY .env /tmp/leaked` would now reach the project-root
  `.env`. Saturn's threat model already treats the Dockerfile as
  trusted (it runs as root in your dev env); this isn't a new concern,
  but worth calling out for anyone sharing a `.saturn/` dir across
  teams.

- **Documentation drift pressure.** The seeded shape is reproduced in
  the README, the workspace component doc, and the engine-pipeline doc.
  All three are updated alongside this decision. A new top-level test
  in the saturn smoke suite would catch future drift but isn't added
  here — saturn has no test suite to graft onto.

## Rejected alternatives

- **Keep `context: .`; document the gotcha.** Pure docs fix, zero code
  change. Rejected: the error when `COPY` fails is confusing, and every
  new user would have to independently discover and fix the shape.
  Documentation doesn't prevent the trap; it just explains the trap
  after someone falls in.

- **Put the Dockerfile at the project root; drop `.saturn/Dockerfile`.**
  Convention used by many projects (`Dockerfile` at the top level,
  compose references it). Rejected: collides with a workspace's own
  Dockerfile (`docker build .` vs `saturn up` would use different
  files). Keeping saturn's files in `.saturn/` preserves the "all my
  saturn-specific config lives in one subdir" convention that the rest
  of 0012 depends on.

- **Auto-migrate existing `.saturn/compose.yaml` on first saturn run
  after upgrade.** Tempting — would avoid surprise when a user finally
  adds a `COPY` and the build breaks on a workspace seeded before 0015.
  Rejected: compose.yaml is user-owned after `saturn new` (0012), and
  silently editing it violates that invariant. Users who care update
  manually, documented in the release note.

- **Make context configurable by a `saturn new` flag.** Adds surface
  for a one-time seed decision that shouldn't vary. Users who want a
  different context edit the file.
