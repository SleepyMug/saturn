# 0009 — Drop path symmetry for mixins; explicit per-slot host-path env vars + host/guest split

> Revises [0008](0008-mixins-as-targeted-bind-mounts.md). Path symmetry for mixin bind-mounts is dropped; the rest of 0008 (selective mixins vs whole-`$HOME`, base-image `setup` splicing, labelled failure when state is missing) stands. Projects-root symmetry (`$HOST_HOME/saturn`) was kept in this revision.
>
> **Revised by [0010](0010-workspace-no-path-symmetry.md).** The projects-root symmetry kept here is now also dropped; the current project is bind-mounted at `/root/<name>` with its host source carried in `SATURN_HOST_WORKSPACE`.

## Context

[0008](0008-mixins-as-targeted-bind-mounts.md) described path symmetry as "load-bearing" — mixin paths bind to the same inside path as on host (`-v $HOST_HOME/.ssh:$HOST_HOME/.ssh`), and `HOME=$HOST_HOME` was injected so `~/.ssh` resolved naturally. This works cleanly when the container should see a 1:1 copy of the host's user state.

It becomes awkward when a project wants **isolation**: a scratch `~/.claude.json` inside a container that doesn't touch the host user's real file. Under path symmetry, the only way to divert is to relocate the host user's own `$HOME/.claude.json`, which is an odd user experience. There's no mechanism to say "for this container, the host source lives here; the container target is its usual place."

Separately, the existence check in 0008 was strict: missing host paths caused `up` to fail. For first-time `saturn up` on a machine without `~/.claude.json`, the user had to `touch` the file (or run `claude login` first). Friction that doesn't add value when saturn is already creating `$HOME/saturn/<name>/` for you.

## Decision

- **Mixin schema becomes slot-based.** Each mixin has `slots: list[{env, target, kind, default_host}]` plus `setup`. `target` is the fixed container-side path (e.g. `/root/.ssh`, `/root/.claude.json`). `env` names the env var that carries the host-side source path. `kind` is `"file"` or `"dir"`. `default_host` is a HOME-relative fallback used only in host mode.
- **Mount model becomes `-v <host_path>:<target>`** — no longer symmetric. Multiple slots per mixin (e.g. `claude` owns `.claude` as dir and `.claude.json` as file) already had this shape; it's now uniform.
- **New `SATURN_IN_GUEST=1` env var.** Outer saturn injects it into child containers. Inner saturn derives `IS_HOST = os.environ.get("SATURN_IN_GUEST") != "1"` at import.
- **Host mode (`IS_HOST`)**:
  - `host_path` resolves from `os.environ.get(env)` or falls back to `f"{HOST_HOME}/{default_host}"`.
  - Missing host paths are auto-created: `mkdir -p` for `dir`, parent `mkdir -p` + `touch` for `file`. A `created <kind>: <path>` line prints per creation so the side effect is visible.
- **Guest mode** (inside a saturn container):
  - Each selected mixin's slot env vars must be set — the outer saturn propagated them. Missing → exit with a labelled list. No defaults. No filesystem writes.
- **`_env_flags` propagates the per-slot vars.** For each resolved slot in play, the child container gets `-e <slot.env>=<host_path>`. `SATURN_IN_GUEST=1` is also added. `-e HOME=$HOST_HOME` is **removed** — the container's `HOME` stays at its image default (`/root`), and slot targets under `/root/` make `~/.ssh`, `~/.claude.json`, etc. resolve naturally.
- **Projects root stays symmetric.** `-v $SATURN_ROOT:$SATURN_ROOT` is unchanged; `SATURN_HOST_HOME` still propagates for that purpose and for host-mode default fallbacks.

## Consequences

- **Explicit isolation is a one-liner**: `SATURN_MIXIN_CLAUDE_JSON=$HOME/saturn/foo/.sandbox/claude.json saturn up foo`. The container writes to its own file; the host user's real `~/.claude.json` stays untouched.
- **Zero-config `saturn up` is friendlier**. First-time users no longer hit "missing path" errors for state they hadn't set up yet — directories and empty files are created on demand, and the print line tells them which.
- **Container `HOME=/root` is now part of the contract**. Mixin targets use absolute paths under `/root/` so tools looking up `~` find them regardless of the image's `USER` or `WORKDIR`. This matches what a native Debian image expects and removes a previously-implicit assumption.
- **Nested mixin usage is more explicit**. An inner `saturn up --mixins ssh` without the outer having selected `ssh` now fails with "SATURN_MIXIN_SSH is not set" instead of the previous "path doesn't exist inside" — the error points at the outer invocation, which is where the fix belongs.
- **Schema migration**: the old `"paths": [".ssh"]` shape is gone. Third-party `MIXINS` customizations need to move to `"slots": [{"env": "SATURN_MIXIN_FOO", "target": "/root/...", "kind": "dir", "default_host": "..."}]`.

## Rejected alternatives

- **Keep path symmetry and add a separate "isolation override" surface.** Would mean two parallel mechanisms (symmetric default + per-project overrides). Slot-based schema unifies them: the env var *is* the override, with a sensible default.
- **Auto-create in guest mode too.** Rejected — the inside saturn doesn't own the host filesystem namespace, and silently creating paths at the container's inside view wouldn't persist them anyway. Guest mode is strict by design.
- **Configurable container target via env var.** Considered (`SATURN_MIXIN_SSH_TARGET=/etc/ssh` etc.), rejected as over-general. Fixed targets match what the tools inside the container expect; no observed need to vary them.
- **Also drop projects-root symmetry.** Rejected for this revision. Projects-root symmetry keeps cwd continuity, `saturn new/ls/rm` from inside containers, and is orthogonal to the mixin blast-radius question this decision addresses.
