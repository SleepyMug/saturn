# Boundary: SATURN_* env vars for nesting

> A tiny two-var contract (`SATURN_IN_GUEST`, `SATURN_SOCK`) — everything else that used to live here (workspace paths, socket paths, per-mixin host paths) is now derived by reverse mount lookup through the engine socket.

## Overview

Previous saturn versions propagated a whole block of env vars for nested-container operation: `SATURN_HOST_WORKSPACE`, `SATURN_WORKSPACE`, `SATURN_HOST_SOCK`, `SATURN_HOST_HOME`, and one `SATURN_MIXIN_<SLOT>` per mixin. All of these existed to let inner saturn translate inside-paths into host-paths when bind-mounting a sibling.

That entire surface collapsed into a single generic mechanism: inner saturn asks the host engine (via the bind-mounted socket) `docker inspect <self>` → `.Mounts` list → translate any inside-path source by finding a mount whose destination is an ancestor and computing `mount.Source + rel`. See [engine](../components/engine/index.md#provided-apis).

What's left in the env contract is the minimum needed to bootstrap that mechanism.

## Host shell (user's interactive terminal)

Vars the user may set to customize host-side saturn:

- `SATURN_SOCK=<path>` — explicit socket path override. Default: first of `$XDG_RUNTIME_DIR/podman/podman.sock`, `$XDG_RUNTIME_DIR/docker.sock`, `/var/run/docker.sock`. Sets `DOCKER_HOST=unix://...` at module import and is re-exported so `${SATURN_SOCK}` substitutes inside workspace `compose.yaml`.
- `SATURN_BASE_IMAGE=<ref>` — override the base image tag.
- `DOCKER_HOST=<uri>` — normally derived from `SATURN_SOCK`. Setting it directly also works.

Vars the user should NOT set on the host:

- `SATURN_IN_GUEST` — meaningful only inside a container. Setting it on host would flip saturn into guest mode and try reverse lookup on the host, which has no outer container to inspect.

## Inside a saturn container

The workspace's `compose.yaml` (seeded by `saturn new`) carries:

```yaml
environment:
  SATURN_IN_GUEST: "1"
  SATURN_SOCK: /var/run/docker.sock
```

Both are static; neither is computed or propagated per-launch.

| Var | Value inside | Role |
|---|---|---|
| `SATURN_IN_GUEST` | `"1"` | Presence ⇒ `IS_HOST=False`. Enables reverse mount lookup + guest-mode build-before-handoff in `_translate_compose`. |
| `SATURN_SOCK` | `/var/run/docker.sock` | Inside path of the bind-mounted host socket. Sets `DOCKER_HOST=unix://...`. Substituted by compose in `${SATURN_SOCK}:/var/run/docker.sock` mounts so the substitution works identically in both modes. |

That's the entire boundary. No host-side paths propagate. The inner saturn learns everything it needs by inspecting its own container through the socket.

Note: `HOME` is not injected by saturn. The container's `HOME` stays at the image default (`/root`). Bind mounts of `${HOME}/.ssh`, `${HOME}/.claude`, etc. land on `/root/.ssh`, `/root/.claude` in guest mode (since `$HOME` substitutes client-side to whatever it currently is), and reverse lookup maps those back to the real host `${HOME}` paths when a sibling is launched.

## Data representation at the boundary

Plain env vars (strings) passed via compose's `environment:` block. `SATURN_IN_GUEST` is the literal string `"1"` (quoted in yaml to avoid getting parsed as an integer).

## Ownership and lifecycle

- Both vars are static workspace-level config, not runtime-propagated. Saturn writes them into the compose.yaml once at `saturn new` time.
- If the user edits compose.yaml to remove them, nested saturn breaks (reverse lookup never fires; compose handoff fails). Document this as a "don't touch these" for the seeded template.
- Nothing else in the SATURN_* namespace is load-bearing for nesting — per-mixin paths, workspace paths, host socket path, host home, all derived on demand.

## Constraints per side

### Host constraints

- Stale `SATURN_IN_GUEST=1` in the user's shell environment (leaked from `saturn shell`) will flip host saturn into guest mode; `_current_container_mounts` then tries to self-inspect the host process (not a container), which fails. Unset it or restart the shell.
- `SATURN_SOCK` pointing at a dead socket yields a build/exec failure from the first engine call. `docker compose config` output mentions "connection refused" — the error is clear enough to debug.

### Container constraints

- `SATURN_IN_GUEST=1` must be in the environment; the outer saturn's `saturn new` template sets it. Removing it from `compose.yaml`'s `environment:` block turns off reverse lookup even inside a container.
- `SATURN_SOCK` must match the container-side bind-mount target of the socket (`/var/run/docker.sock` by saturn convention). Changing one without the other breaks DOCKER_HOST.
- Do not override `hostname:` in the workspace's `compose.yaml`. Saturn self-inspects by `socket.gethostname()` (default: short container id). Overriding the hostname breaks reverse lookup — `docker inspect <your-hostname>` will only succeed if you also named the container that.
