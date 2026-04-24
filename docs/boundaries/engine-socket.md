# Boundary: container ↔ host engine socket

> The bind-mounted `/var/run/docker.sock` — container-root maps to host-user under rootless userns, so no sudo is needed inside.

## Overview

Saturn containers reach the host's container engine by bind-mounting the host's engine socket at `/var/run/docker.sock` inside. The container runs as root (no custom user, no sudo). Under rootless engines, the user namespace maps container-uid 0 back to the invoking host user (e.g. uid 1000), so the container-root process appears to the host kernel as the socket owner — `open()` succeeds without any further privilege trick.

Saturn itself uses the socket for three things: (a) talking to the host engine via `docker` CLI (`DOCKER_HOST=unix://...`); (b) running `docker compose` against the host engine; (c) self-inspection (`docker inspect <hostname>`) to retrieve the current container's bind-mount list for reverse path translation.

## Both sides' perspective

**Host side** (the engine exposing the socket):

- Rootless podman: `$XDG_RUNTIME_DIR/podman/podman.sock`, owned by the invoking host user (e.g. `guest:guest` uid 1000).
- Rootless Docker: `$XDG_RUNTIME_DIR/docker.sock`, owned by the invoking host user.
- Rootful Docker (off-path): `/var/run/docker.sock`, owned `root:docker` mode 0660.

**Container side** (the bind-mount consumer):

- The socket appears at `/var/run/docker.sock` (saturn convention; `DOCKER_HOST=unix://...` points here).
- Container process runs as container-uid 0 (root).
- Under **rootless** engines: host uid 1000 → container uid 0. The socket and the container process are the same principal — `open()` succeeds, and files written into bind-mounted host trees land as the invoking host user.
- Under **rootful** engines: no userns remapping. Container uid 0 = host uid 0, so `open()` on the socket still succeeds — but every file the container writes into a bind-mounted host tree (workspace source, `~/.ssh`, `~/.claude`, ...) lands as `root:root` on host. Saturn's other invariants assume the rootless mapping; see "Rootless is load-bearing" below before pointing `SATURN_SOCK` at a rootful daemon.

## Data representation at the boundary

Unix domain socket carrying the Docker Engine HTTP API (used by both docker and podman's docker-compat listener). Saturn's side always speaks this via the `docker` CLI (with the `compose` plugin) and `DOCKER_HOST=unix:///var/run/docker.sock`.

## Ownership and lifecycle

- The socket's lifecycle is managed by the host (systemd user unit for `podman.socket`, etc.). Saturn never creates or destroys it.
- Saturn's role is only to bind-mount the host socket into each container it launches at `/var/run/docker.sock`. In practice this is declared in the workspace's `compose.yaml` via `- ${SATURN_SOCK}:/var/run/docker.sock` (seeded by `saturn new --socket`).
- Inside a saturn container, `SATURN_SOCK=/var/run/docker.sock` is set in the compose-level environment; compose substitutes this at `config` time before the spec is evaluated. The reverse-lookup step then translates the inside path back to the real host socket path when preparing specs for child workspaces.

## Constraints per side

### Container side

- Runs as container-root; no sudo is invoked.
- Must not reach for `podman` directly. That bypasses the socket and opens the rootless store — which (if the same rootless store) races with the host's serialized mutations.

### Host side

- Socket must be listening before any `saturn <cmd>` runs. `systemctl --user enable --now podman.socket` is the one-time setup for rootless podman; rootless Docker's equivalent depends on the installer.
- Permission model on the socket file is the engine's concern; saturn does not chmod/chown it.

## Security note

Bind-mounting this socket grants full control of your engine to the container — privileged sibling with `/` mounted is possible. Saturn additionally bind-mounts whatever the workspace's `compose.yaml` declares — typically the workspace dir at `/root/<basename>`, plus any mixin-style bind mounts `saturn new --ssh/--claude/...` generated (e.g. `${HOME}/.ssh:/root/.ssh`). The blast radius is whatever that compose file lists. This is acceptable for a personal dev tool, not acceptable for untrusted code.

`IS_SANDBOX=1` in the base image tells tools like Claude Code that running as root is intentional — it is not an actual sandbox.

## Rootless is load-bearing

The rootless userns mapping (container-uid 0 → host user) is the reason saturn can run as root inside without poisoning the host. Two things fall out of that assumption that do not survive pointing at a rootful daemon:

- **File ownership cascade.** Under rootful, any file the container writes into a bind-mounted host tree is owned by `root:root` on host. This is not limited to the mount points — atomic-save editors (vim, most IDEs) `rename()` new inodes into place, so just editing a workspace file from inside flips its host owner to root. `git` writes root-owned blobs under `.git/objects/`. Config-dir mixins are worse: `~/.ssh`, `~/.config/gh`, `~/.claude`, `~/.claude.json`, `~/.codex` will accumulate root-owned files that the host user can no longer rewrite. `ssh` in particular enforces strict ownership on `~/.ssh/` and its keys — one root-owned append to `known_hosts` from inside a rootful container breaks host-side ssh until you `chown` it back.
- **Threat-model collapse.** A bind-mounted socket is always "root on the engine" — but under rootless that engine's blast radius is capped at your host user. Under rootful it is the host machine: container-root can `docker run --privileged -v /:/host ...` and escalate. Anything untrusted inside the container (a compromised dep, an LLM session, a CI task) inherits that capability.

Saturn prints a one-line warning to stderr on startup when it detects the chosen socket is owned by uid 0 and the caller is not root — e.g. when `SATURN_SOCK=/var/run/docker.sock` is the only available socket, or the user set it explicitly. The warning is advisory, not a gate. If you genuinely need to run saturn against a rootful daemon, the supported escape hatch is Docker's `userns-remap` (maps rootful container-root to a subuid range, restoring the rootless ownership story at the daemon-config level).
