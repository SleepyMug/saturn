# Bind-mount source validation on `saturn up`

## Context

`saturn new` uses `_AUTOCREATE` to materialize missing host-side bind
sources under `$HOME` — `mkdir` for dirs, `touch` for files — so the
fresh workspace can come up cleanly. That logic runs **once**, at
workspace creation, for the subset of sources seeded by the template
flags (`--ssh` → `$HOME/.ssh`, `--claude` → `$HOME/.claude` +
`$HOME/.claude.json`, etc.).

Anything missing at `saturn up` time is left to docker, whose default
for a missing bind source is "create a directory". For dir targets
that's usually fine. For **file** targets (`$HOME/.claude.json` being
the canonical example), docker still creates a directory at the
source path, which poisons the workspace — tools then see a dir where
they expect a file, and on a rootful engine the accidental dir is
owned by `root:root`.

This is latent today but surfaces the moment a compose.yaml uses
file binds that aren't created by the `saturn new` template — e.g.
galaxy-mini's in-progress plan to have the dev `.saturn/compose.yaml`
project host `$HOME` creds into the repo's `state/*` subpaths, where
`$HOME/.claude.json:/root/<ws>/state/.claude.json` is a file bind
that no `saturn new` flag seeded. See
`../../galaxy-mini/.saturn/compose.yaml` and the galaxy-mini plan at
`~/.claude/plans/read-about-the-project-rustling-book.md`.

## Decision (proposed)

Extend saturn's pre-flight (inside `_translate_compose`, or a new
pre-up step) to validate every bind-mount source in the merged spec
before the `docker compose up` hand-off:

1. For each service's `volumes:` entry with `type: bind` (after
   compose-config normalization so the source is absolute):
   - **Host mode**: check the source exists on the host. If not,
     create it — but saturn must know whether to create a file or a
     directory.
   - **Guest mode**: source has already been reverse-translated to a
     host path via `.Mounts`; same check applies, but saturn can't
     `mkdir`/`touch` on the host directly. Fail with a clear error
     pointing at the missing path instead.

2. **Source-type inference** — three plausible strategies, pick one
   or allow them in combination:
   - **(a) `x-saturn.autocreate` compose extension**: explicit
     declaration per volume, e.g.
     ```yaml
     - ${HOME}/.claude.json:/root/.claude.json
     x-saturn:
       autocreate: file    # or "dir"
     ```
     Compose extensions survive `compose config` because they're
     under `x-*`. Cleanest semantically; requires authors to annotate.
   - **(b) Filename heuristic**: treat known-file patterns
     (`*.json`, `*.yaml`, `*.yml`, `*.conf`, `*.toml`, leaf name
     without a trailing slash and without a dir-only suffix) as
     files, everything else as dirs. Zero authoring cost; brittle on
     edge cases.
   - **(c) Target-path inspection**: run `docker image inspect` on
     the service's image to look at the target-path type in the
     image's filesystem — if `/root/.claude.json` is a file in the
     image, create a file on the host. Requires the image to already
     be pulled / built; chicken-and-egg on first run.

   Recommended: (a) as the authoritative signal, (b) as the fallback
   when the annotation is absent. Never (c) — the image-first-run
   ordering is ugly.

3. **Behavior in `saturn new`**: `_AUTOCREATE` can be replaced by
   the same machinery — `saturn new` just pre-materializes sources
   by running the validation in "create-if-missing" mode before the
   template's first `saturn up`. One code path for both entry points.

4. **Ordering vs. guest-mode translation**: the validation must run
   **after** reverse-mount translation in guest mode (otherwise the
   source is still an inside-container path, not a host path). Slot
   it at the tail of `_translate_compose`, just before writing
   `compose.json`.

## Out of scope

- Cleanup of wrongly-typed sources (a dir that should be a file).
  Detect and fail with a clear message; don't mutate.
- Managing symlinks. If the source is a symlink, resolve once and
  validate the target; leave the symlink alone.
- Adding `autocreate: "smart"` modes, periodic cleanup, etc. Keep
  the scope to "pre-up: every bind source exists, with the right
  type, or fail loudly."

## Consequences

- galaxy-mini's `state/*` projection plan (`.saturn/compose.yaml`
  projecting host `$HOME/.claude.json` into `/root/<ws>/state/.claude
  .json`) stops needing a manual `touch state/.claude.json` before
  `saturn up`.
- `saturn new` shrinks — autocreate stops being a special per-flag
  branch and becomes a special case of pre-up validation.
- Compose files gain an optional `x-saturn:` namespace, which sets
  precedent for future saturn-specific metadata (good — better than
  abusing env vars or compose comments).

## Verification plan (for the implementation task)

1. Unit-level: `_translate_compose` round-trip with a compose that
   declares an `x-saturn.autocreate: file` on a missing source →
   source materialized as an empty file.
2. Same, `autocreate: dir` → source materialized as dir.
3. Guest mode, missing source, no mount covers it → clear error
   citing `bind-mount source … does not exist; and guest saturn
   cannot create host files`.
4. Integration: galaxy-mini's `state/.claude.json` projection comes
   up cleanly on a fresh clone with no manual `touch` / `mkdir`.

## Open questions

- Should the `autocreate: file` case refuse to run if the saturn
  process has no permission to create the source path? Or silently
  fail and let docker's error surface? Lean: refuse with a clear
  error — consistency with the guest-mode case.
- Should `saturn down` ever clean up sources it created? **No** —
  credentials and state outlive one `up/down` cycle.
