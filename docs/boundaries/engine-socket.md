# Boundary: container ↔ host engine socket

> The bind-mounted `/var/run/docker.sock` is a user-namespace boundary: which inside-uid can open it depends on rootless vs rootful and on sudo use.

## Overview

saturn containers reach the host's container engine by bind-mounting the host's engine socket at `/var/run/docker.sock` inside. The container is non-root (`agent`, uid 10001 inside); the socket is owned by the host user (rootless podman/Docker) or by root+docker-group (rootful). Whether `agent` can open the socket directly, and whether saturn inside the container must prepend `sudo`, depends on the host setup.

## Both sides' perspective

**Host side** (the engine exposing the socket):

- Rootless podman: `$XDG_RUNTIME_DIR/podman/podman.sock`, owned by the invoking host user (e.g. `guest:guest` uid 1000).
- Rootless Docker: `$XDG_RUNTIME_DIR/docker.sock`, owned by the invoking host user.
- Rootful Docker (off-path, but works): `/var/run/docker.sock`, owned `root:docker` mode 0660.
- Rootful podman with systemd socket unit (off-path): `/run/podman/podman.sock`, owned by root.

**Container side** (the bind-mount consumer):

- The socket appears at `/var/run/docker.sock` (saturn convention; `DOCKER_HOST=unix://...` points here).
- The apparent owner inside the container is determined by the user namespace mapping of the host owner:
  - **Rootless** engines: host uid 1000 → container uid 0. The socket appears owned by container-root.
  - **Rootful** engines: no userns remapping. Socket appears owned as on host (root, gid docker).
- `agent` (container uid 10001) is either:
  - Under rootless: mapped to a subuid (~110000 on host). Unrelated to the socket's owner at the host level. Cannot open the socket without elevating.
  - Under rootful: real uid 10001 on host. Also can't open a 0660 socket owned by root unless added to the docker group.

## Data representation at the boundary

Unix domain socket carrying the Docker Engine HTTP API (used by both docker and podman's docker-compat listener). saturn's side always speaks this via the `docker` CLI with `DOCKER_HOST=unix:///var/run/docker.sock`.

No serialization concerns on saturn's end — the CLI handles it.

## Ownership and lifecycle

- The socket's lifecycle is managed by the host (systemd user unit for `podman.socket`, systemd user unit for rootless `docker.service`, etc.). saturn never creates or destroys it.
- saturn's role is only to bind-mount `$HOST_SOCK` into each container it launches at `/var/run/docker.sock`. No refcounting; the mount goes away when the container does.
- `$HOST_SOCK` propagates into nested saturn via the `SATURN_HOST_SOCK` env var so the *innermost* saturn still knows the host's path — see [nested-env.md](nested-env.md).

## Constraints per side

### Container side (`agent` in a saturn container)

- **Must sudo.** `saturn` inside sets `USE_SUDO=1`, so every `docker ...` call becomes `sudo docker ...`. Under rootless engines, `sudo` → inside-uid 0 → host uid 1000 (the socket owner) → permission passes. Under rootful, `sudo` → inside-uid 0 = host uid 0 (root) → permission passes. One recipe covers both. See [decisions/0003-sudo-over-group-add.md](../decisions/0003-sudo-over-group-add.md).
- **Cannot open socket without sudo under rootless**, even if `agent` is added to arbitrary groups, because the socket's group (the host user's group, mapped to container gid 0) isn't a group a non-root user can meaningfully join in a user namespace.
- **Must not mix engines inside.** If inside saturn reaches for `podman` directly (e.g. via an alias), it bypasses the socket entirely and opens the storage directory — which (if it's the same rootless store) races with the host's serialized mutations.

### Host side

- Socket must be listening before any `saturn <cmd>` runs. `systemctl --user enable --now podman.socket` is the one-time setup.
- The socket file permission model is the engine's concern; saturn does not chmod/chown it.

## Security note

Bind-mounting this socket is equivalent to granting full control of your engine to the container. `agent` inside a saturn container with sudo can manipulate anything the host engine can — including creating a privileged sibling that mounts `/`. This is acceptable for a dev tool; do not expose the pattern to production containers or untrusted code.
