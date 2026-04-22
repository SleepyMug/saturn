# 0014 — Multi-file compose support; override chain replaces per-caller compose generation

> Extends [0012](0012-compose-native-wrapper.md). Saturn's pass-through now
> accepts multiple compose files and layers them via `docker compose -f base
> -f override …`. Programmatic callers (like galaxy-mini's millworker) that
> used to generate a whole replacement `compose.yaml` per session now emit
> only a small delta. Human-editable workspaces are unchanged.

## Context

Since [0012](0012-compose-native-wrapper.md), saturn has been a thin wrapper
around `docker compose` with one source of truth: `.saturn/compose.yaml`.
That's right for a human dev loop where the committed file represents "how
I run this workspace." It's awkward for programmatic callers that want to
parameterize a few fields per invocation — container name, image tag, extra
env, extra bind mounts — without touching the committed file.

The pre-0014 workarounds were ugly:

- **Generate a whole replacement `compose.yaml` per session** in a scratch
  dir and point saturn at it by running from there. Loses the connection
  to the workspace entirely — nested-inspect, path-walking, all of it.
- **Overwrite the committed file in a disposable clone.** Works, but
  duplicates every field the committed file already has, and makes the
  scratch compose diverge silently from the committed one.

Docker compose solves this natively: `docker compose -f base -f override …`
merges the files before any other processing. Scalars replace, lists append,
maps deep-merge. `compose config` emits the merged spec. It's the same
mechanism devcontainer's `dockerComposeFile: [a, b]` uses.

Saturn just needed to stop hard-coding a single `-f`. Once it accepts a
list, the whole pipeline — env substitution, path normalization, reverse
mount lookup in guest mode — applies to the *merged* spec for free,
because it all runs after `docker compose config`.

## Decision

- **Pass-through accepts multiple compose files.** `passthrough(argv)`
  discovers overrides before translation and passes every file to
  `docker compose config` with repeated `-f` flags. Order matters (later
  files layer on top); saturn preserves discovery order.

- **Two discovery sources, auto-applied in this order:**

  1. **`.saturn/compose.yaml`** — the committed base. Always first; sort
     order within the workspace doesn't apply.
  2. **`.saturn/compose.override*.yaml`** in the workspace — globbed and
     lexically sorted. Matches docker compose's own `compose.override.yaml`
     convention. Ergonomic path for humans: drop a
     `.saturn/compose.override.local.yaml` next to the committed base for
     machine-local tweaks; keep it out of git.
  3. **`SATURN_COMPOSE_OVERRIDES`** — colon-separated absolute paths, in
     the spirit of docker's `COMPOSE_FILE`. The programmatic path:
     callers that don't want a file on disk in `.saturn/` can set this
     env var instead. Empty segments are skipped.

  The workspace `.saturn/compose.override*.yaml` glob comes before the env
  var so explicit env-var layering can override committed overrides when
  that matters.

- **`_translate_compose(compose_files: list[Path], project: str) -> Path`** —
  the helper now takes the ordered list. It hands every file to
  `docker compose config --format json`; compose does the merge and
  substitution; saturn post-processes the canonical JSON exactly as
  before. Reverse mount lookup in guest mode applies to the already-
  merged bind-mount sources.

- **No new commands, no new arg parsing.** Override discovery is implicit
  in `passthrough`. `saturn new`, `saturn base *`, `saturn shell` are
  unchanged — seeding and base-image management don't meaningfully
  interact with overrides.

- **`.saturn/compose.json` (derived, written every invocation) reflects
  the merged spec.** Users who want to see the effective spec after
  overrides inspect that file, or run `saturn config`.

## Consequences

- **Programmatic callers emit deltas, not replacements.** Galaxy-mini's
  millworker went from "generate a full replacement compose.yaml in a
  scratch dir" to "write a 7-line `.saturn/compose.override.yaml` in the
  clone." The committed base carries the workspace-stable fields (image
  build, working_dir, workspace bind, `IS_SANDBOX=1`); the override
  carries only per-session fields (container_name, image tag, extra env,
  extra binds). Smaller diffs, less duplication, one compose lineage.

- **Human-editable overrides are now a first-class path.** A
  `.saturn/compose.override.local.yaml` (gitignored) lets a user set
  resource limits, extra mounts, or a different port mapping without
  touching the committed compose. Previously users had to patch the
  committed file and remember to revert.

- **Guest-mode translation applies to merged sources uniformly.** Because
  the merge happens inside `docker compose config` — before saturn's
  reverse mount lookup runs — a bind mount declared only in an override
  is reverse-resolved like any bind mount declared in the base. No
  special-casing.

- **Override files participate in env substitution just like the base.**
  `${HOME}`, `${SATURN_SOCK}` in an override resolve the same way. This
  falls out of using `docker compose config` for the merge; we'd have
  had to reimplement it if we'd merged files ourselves.

- **Discovery is conservative — sorted glob only.** Arbitrary directory
  recursion would make the layering impossible to predict. The two
  documented sources (workspace glob + env var) are explicit and
  easy to reason about.

- **Behavioral surface widens slightly.** A user who happens to have
  `compose.override.local.yaml` in `.saturn/` pre-0014 — unlikely but
  possible — will see it applied to every pass-through after upgrade.
  Mitigation: release note; the convention-matching filename is already
  a strong signal.

## Rejected alternatives

- **Accept `-f` on the saturn command line.** `saturn -f extra.yaml up`.
  Looked tempting because it mirrors `docker compose -f`. Rejected: it
  collides with saturn's pass-through design — `-f` would have to be
  intercepted before argv is forwarded, which reintroduces the
  argparse-on-pass-through entanglement that 0012 explicitly removed.
  Env-var + auto-glob avoids the parser entirely.

- **Hard-code `.saturn/compose.override.yaml` (singular) only.** Matches
  docker compose's default exactly. Rejected: the common case for
  programmatic callers is a unique per-session file, and forcing them to
  clobber a single well-known path serializes concurrent sessions
  sharing a workspace dir. Glob + env var together cover both the human
  case (the singular convention file) and the machine case (per-session
  file written to a unique path) without either interfering with the
  other.

- **Merge files ourselves before passing to compose.** Possible: load each
  YAML, deep-merge dicts, pass a single synthesized file to compose.
  Rejected: docker compose's merge semantics are non-trivial (list
  append vs replace rules differ across keys) and are documented by
  compose itself; reimplementing would drift over time. Leaning on
  `compose config` keeps saturn out of the compose-semantics business.

- **Profile-based overrides (`docker compose --profile`).** Docker compose
  already supports profile-gated services. Too narrow: profiles gate
  service *presence*, not service-field overrides. Wrong tool for the
  "same service, tweaked fields" case that motivates this change.

- **A `saturn compose` subcommand that emits the merged JSON.** Nice for
  debugging but redundant — `.saturn/compose.json` is written every
  pass-through and contains exactly the merged+translated spec. Users
  who want it run any saturn command (even `saturn ps`) and read the
  file.
