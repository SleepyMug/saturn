# 0006 — User-state mixins: install-in-base + user-global volumes

> Per-user state like `~/.ssh`, `~/.config/gh`, `~/.claude`, `~/.claude.json`, `~/.codex`, `~/.emacs.d`, `~/.config` enters project containers via named **mixins**: an optional install snippet spliced into the base Containerfile plus a user-global named volume mounted at the target path inside the project container.

## Context

Dev-container workflows routinely need access to user-global state that is not project-specific: SSH keys for pushing to remotes, `gh` tokens for GitHub API, Claude/Codex auth, npm credentials, editor config, etc. Before this change, saturn had no first-class support for any of it.

The obvious options were all unsatisfying:

1. **Bind-mount host directories.** Violates [0002](0002-volume-first-zero-host-state.md) (zero host state), leaks host paths into the nested-env contract ([boundaries/nested-env.md](../boundaries/nested-env.md)), and couples saturn to host-side layout conventions (XDG, macOS vs Linux, etc.).
2. **Re-configure state per-project.** Tedious, error-prone, and wasteful — auth tokens legitimately *are* user-global, and a per-project re-login is just friction.
3. **Inline everything into each project's `.saturn/Containerfile`.** Pushes user-global concerns into the project's committed artifacts; every project forks its own setup.

## Decision

Add a small static registry of **mixins** inlined in `saturn` (`MIXINS: dict[str, dict]`). Each mixin declares:

- a **`target`** — absolute path inside the container where state lives,
- a **`command`** — optional shell string, spliced into the base Containerfile as `RUN <command>` so the tool is installed at base-image build time,
- an optional **`subpath`** — for file-target mixins (e.g. `~/.claude.json` is a single file, not a directory), specifies the filename within the volume that `--mount ...,volume-subpath=<subpath>` exposes at the target path.

Volumes are named `saturn_mixin_<name>` (with `-` → `_`), labeled `saturn.volume=mixin` + `saturn.mixin=<name>`, and are user-global: they are never touched by `project rm` and live outside all project lifecycles.

Three CLI surfaces use mixins:

- `base template --mixins <csv>` / `base default --mixins <csv>` — splice mixin install lines between the base packages block and the `COPY saturn` step.
- `up <name> --mixins <csv>` — mount the selected mixin volumes in the project container at their targets.
- `project config [--mixins <csv>]` — base-image shell with only mixin volumes mounted (defaults to all mixins when omitted) for interactive state setup: `ssh-keygen`, `gh auth login`, etc.

`base build <file>` intentionally does **not** accept `--mixins` — user-supplied Containerfiles are used verbatim. To combine a custom base with mixins: `base template --mixins ... > my.Containerfile`, edit, `base build my.Containerfile`.

## Alternatives Considered

- **Positional mixin arg** (`saturn up myproj ssh,gh`) — rejected; `--mixins` is more extensible and less ambiguous when combined with other flags.
- **Containerfile-snippet `command`** (raw Dockerfile fragment vs shell string) — rejected raw snippets; shell strings are simpler and cover all current use cases (one `RUN <cmd>` per mixin). If complex install sequences emerge later, upgrading the schema is cheap.
- **Mixin volumes per-project** — rejected; the whole point is to share auth/keys across projects without reconfiguration.
- **Externally-configured mixins** (e.g. `~/.saturn/mixins.toml`) — rejected; violates [0001](0001-single-file-distribution.md) and [0002](0002-volume-first-zero-host-state.md). Adding/changing a mixin is editing `saturn` itself — aligns with the rest of the configuration model.
- **Splicing mixins into `base build` too** — rejected; mutating a user-supplied Containerfile invisibly is surprising. The explicit template-then-edit path keeps the user in control.
- **Wipe mixin volumes on `project rm`** — rejected; they are user-global state, unrelated to project identity. A hypothetical future `mixin rm` would handle intentional wipe.

## Consequences

- **New user-global resources** live in the engine store: one volume per *used* mixin. They survive `project rm` and persist across saturn versions.
- **Nested mixin targets are safe.** Docker and podman both reorder mounts shortest-prefix-first (see [experiment_journal/mount-ordering-nested-vs-duplicate-targets.md](../experiment_journal/mount-ordering-nested-vs-duplicate-targets.md)). Combining e.g. `xdg-config` with `gh` produces a layered view where the `gh` volume owns `/home/agent/.config/gh` and the `xdg-config` volume owns everything else under `/home/agent/.config`. Saturn prints an advisory `note:` via `_check_mount_overlap` so the interaction is visible.
- **Exact-target collisions fail fast.** `_check_mount_overlap` runs before every `docker run` that involves mixin mounts, covering the socket path, ws mount, and each mixin target in a single pass.
- **Engine version requirement** for file-target mixins: `volume-subpath=` needs Docker 25.0+ or Podman 4.7+. Directory-target mixins work everywhere that supports named volumes.
- **Defaults are uniform across entry points.** `DEFAULT_MIXINS = ["ssh", "claude", "claude-json", "codex", "gh"]` is the set every mixin-aware command (`base template`, `base default`, `up`, `project config`) uses when `--mixins` is omitted. The first-use auto-build via `ensure_base()` also uses these defaults, so a bare `saturn up <name>` produces a container with consistent base installs and mounted state. Explicit `--mixins ''` opts out; `--mixins <csv>` picks a different set. Chosen because these cover the overwhelming majority of dev-container sessions (SSH keys, Claude Code + its `~/.claude.json` config, Codex, GitHub CLI); the remaining built-ins (`emacs`, `xdg-config`) are opt-in.
- **The naming `project config` is slightly awkward** — it doesn't operate on a single project. But it belongs with `project shell` as an interactive base-image shell operation, and the group provides discoverability.
