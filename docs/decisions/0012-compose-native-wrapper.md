# 0012 — Saturn becomes a compose-native wrapper; reverse mount lookup replaces path/env plumbing

> Revises [0011](0011-workspace-as-path.md). Saturn is now a thin wrapper around `docker compose`. The entire imperative `docker run` surface (`cmd_up` / `cmd_down` / `cmd_shell` / `cmd_exec` / `_env_flags` / `_base_mount_flags` / `_mixin_mount_flags`) is gone, along with the mixin registry and every SATURN_* env var whose sole purpose was to translate inside-paths to host-paths for nested invocations. Paths are translated at runtime by inspecting the current container's own `.Mounts` through the bind-mounted engine socket. Mixins move from a runtime concept to `saturn new --<flag>` template fragments.

## Context

Across 0009–0011 saturn accreted machinery for one recurring problem: nested-container path translation. Each mixin slot got its own env var so inner saturn could learn the host path; workspace translation added `SATURN_HOST_WORKSPACE` + `SATURN_WORKSPACE`; the socket had `SATURN_HOST_SOCK`; mixin defaults referenced `SATURN_HOST_HOME`. Every time a new thing wanted to cross the boundary, a new env var was added.

Two observations collapse the machinery.

**First**: `docker compose config --format json` is a complete, correct parser of compose semantics — env substitution, relative-path resolution, short-form → long-form volume normalization. Using it means saturn doesn't parse anything compose-shaped; saturn just post-processes the canonical JSON.

**Second**: inside a saturn container, `docker inspect <self>` through the bind-mounted socket returns the current container's full `Mounts` list: every inside destination with its real host source. Translating any inside-path bind-mount source from the child compose is then a generic lookup — find the mount whose destination is an ancestor, replace the source with `mount.Source + rel`. This subsumes `SATURN_HOST_WORKSPACE` + `SATURN_WORKSPACE`, `SATURN_HOST_SOCK`, and every `SATURN_MIXIN_*` with one mechanism.

Once the runtime path-translation machinery collapses, the imperative `docker run` flow does too — compose can express everything saturn was hand-rolling (bind mounts, env, workdir, init, container name, labels). What remains saturn-specific is a template generator (`saturn new --<flag>`) and the translation pipeline.

## Decision

- **`saturn <anything except new/base/shell>` is a pass-through to `docker compose`.** Saturn reads `.saturn/compose.yaml`, runs `docker compose config --format json`, post-processes (translates in guest mode), writes `.saturn/compose.json`, and invokes `docker compose -f compose.json -p <workspace-basename> <original-argv>`. The full compose surface (`up`, `up -d`, `down`, `logs`, `ps`, `exec`, `restart`, `build`, …) is available without per-command wiring.
- **Two env-var contract for nesting**: `SATURN_IN_GUEST=1` + `SATURN_SOCK=/var/run/docker.sock`. Both are static; neither carries per-launch data. Everything else — host socket path, host home, workspace host path, each mixin slot's host path — is derived on demand by engine inspect.
- **Reverse mount lookup as the translation primitive**. In `_translate_compose`:
  1. Call `docker inspect $(socket.gethostname())` to get the current container's `.Mounts`.
  2. For each bind-mount source in the resolved child spec, find the mount whose destination is the longest ancestor; replace the source with the mount's host source + relative suffix.
  3. Unresolvable sources → fail fast with the list.
- **Build contexts in guest mode**: pre-build with `docker build` (client reads the inside-path context; daemon stores result on host engine), then strip `build:` from the service in the spec so compose doesn't try to re-read the context itself. Without this, compose's client streams the build context from *its* filesystem (inside), but a translated host path doesn't exist inside.
- **Mixins become `saturn new` template fragments.** `--ssh`, `--gh`, `--claude`, `--codex` append install blocks to `.saturn/Dockerfile` and bind-mount lines to `.saturn/compose.yaml`. `--socket` appends only the socket bind mount. Host-mode auto-create remains, keyed to the selected flags. The runtime `MIXINS` registry, `_resolve_mixin_slots`, `_ensure_mixin_host_paths`, `_mixin_mount_flags`, `_render_base_containerfile` and `base template` are gone.
- **Base image shrinks.** `localhost/saturn-base:latest` is Debian trixie + `docker-cli` + `docker-compose` plugin + `python3` + `git` + `curl` + the saturn script + `ENV IS_SANDBOX=1`. No ssh/gh/nodejs/claude/codex — those live in per-workspace Dockerfiles.
- **`saturn shell` survives** as a thin alias (rewrites argv to `exec dev bash` and falls through to pass-through). It's the one command common enough to pave.
- **`up` is foreground by default** (matches compose). Previous saturn forced `-d`; users who want detach pass `-d` explicitly.
- **No positional target args on lifecycle commands.** `up`, `down`, `exec`, `logs` all derive the workspace by walking cwd upward for `.saturn/compose.yaml`. `cd` to switch. (Pre-compose saturn took `[dir]` on `up`/`new`; `new` keeps it, nothing else does.)

## Consequences

- **Massive simplification.** The saturn script shrinks from ~630 lines to ~300, most of which is templates. The entire `Workspace` class, `_resolve_target` path arithmetic, `_env_flags` block, `_base_mount_flags`, `MIXINS` registry, and the six mixin helper functions are deleted.
- **One compose.yaml, both modes.** `${HOME}/.ssh`, `${SATURN_SOCK}:/var/run/docker.sock`, `..:/root/<name>` — all substitute naturally on host (to real host paths) and in guest (to inside paths that reverse lookup translates). No more `if IS_HOST: ... else: ...` in the user-facing template.
- **Full compose surface is free.** `saturn logs -f`, `saturn ps`, `saturn restart dev`, `saturn up --build`, `saturn exec dev bash`, `saturn down --volumes` all work without per-command handlers.
- **Users edit compose.yaml directly.** Adding a bind mount, setting resource limits, declaring a sidecar network — edit the yaml. The seeded templates cover the common case; anything beyond is just compose.
- **The hostname-based self-inspect has a documented limitation.** Overriding `hostname:` in compose.yaml breaks reverse lookup. Saturn reports it clearly when it does break, pointing at the field.
- **Guest-mode build is indirect but transparent.** Saturn runs `docker build` itself before handing to compose; users see the two-phase progress. Cache hits work normally because it's the same host engine building either way.
- **One hard dep gained.** The `docker` CLI now needs the `compose` plugin. Debian's `docker-compose` package provides it; so does Docker's own installer. Rootless podman users already run compose via `docker compose` against the compat socket.
- **No more cross-container mixin plumbing.** The design surface for "I want my .ssh to show up inside" is now a line in compose.yaml, not a magic saturn flag. If a user wants to override a credential path, they edit the compose file — no more per-slot `SATURN_MIXIN_<SLOT>` env var and no `--mixin-root` flag.
- **Behavioral regression: `saturn up` is no longer detached by default.** Documented in the README and command table. Users who relied on the detached default add `-d`.
- **`saturn ls` / `saturn rm` stay gone** (they were already gone in 0011). `docker ps --filter name=saturn_` + `docker rmi` cover the uses.

## Rejected alternatives

- **Keep the imperative `docker run` surface, add compose as an opt-in.** Doubles the code. The compose surface already covers everything imperative saturn did; keeping both is maintenance overhead with no ergonomic upside.
- **Parse compose.yaml ourselves with `yq` (originally proposed in this design)**. `docker compose config --format json` is strictly better: it handles env substitution, relative paths, extends, includes, and any future compose-spec additions. yq would re-implement a subset and drift. Dropped.
- **Reverse lookup via `/proc/self/mountinfo`** (bypasses the engine socket). Works, but mountinfo's source paths under rootless podman can show storage-internal paths for some bind types, and the encoding of bind subpaths is subtle. `docker inspect` returns the exact `Source` field the daemon has recorded — no parsing of kernel formats. The socket is mounted anyway (required for everything else saturn does), so using it as the lookup path adds no new dep.
- **Pass-through `hostname:` overrides by adjusting self-inspect to match**. Complicated: saturn would need to know what hostname the outer-launched container got, propagate it, etc. Reverts to the env-var plumbing we're trying to remove. Document the constraint instead: don't override `hostname:` in saturn workspaces.
- **Keep the `MIXINS` registry and generate `compose.yaml` fragments from it at `new` time**. Saves some template duplication but adds indirection. Template fragments inline in `_DF_INSTALL` / `_COMPOSE_VOLUMES` dicts are ~20 lines total — not worth an abstraction.
- **Translate build context in guest mode (instead of pre-building)**. The compose client reads the build context from its local filesystem before streaming to the daemon; a translated host path doesn't exist inside the container, so compose would fail to prepare the context. Pre-building with the inside-path context and stripping `build:` sidesteps this cleanly.
- **Dynamic-pacing both `up` and `down` to keep the detached default**. Adding saturn-specific `up`-rewriting-to-`-d` is a layer of magic on an otherwise pure pass-through. Better to match compose semantics and let users type `-d` when they want detach.
