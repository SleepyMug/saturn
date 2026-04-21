# Boundary: container ↔ host engine socket

> The bind-mounted `/var/run/docker.sock` — container-root maps to host-user under rootless userns, so no sudo is needed inside.

## Overview

saturn containers reach the host's container engine by bind-mounting the host's engine socket at `/var/run/docker.sock` inside. The container runs as root (no custom user, no sudo). Under rootless engines, the user namespace maps container-uid 0 back to the invoking host user (e.g. uid 1000), so the container-root process appears to the host kernel as the socket owner — `open()` succeeds without any further privilege trick.

## Both sides' perspective

**Host side** (the engine exposing the socket):

- Rootless podman: `$XDG_RUNTIME_DIR/podman/podman.sock`, owned by the invoking host user (e.g. `guest:guest` uid 1000).
- Rootless Docker: `$XDG_RUNTIME_DIR/docker.sock`, owned by the invoking host user.
- Rootful Docker (off-path): `/var/run/docker.sock`, owned `root:docker` mode 0660.

**Container side** (the bind-mount consumer):

- The socket appears at `/var/run/docker.sock` (saturn convention; `DOCKER_HOST=unix://...` points here).
- Container process runs as container-uid 0 (root).
- Under **rootless** engines: host uid 1000 → container uid 0. The socket and the container process are the same principal — `open()` succeeds.
- Under **rootful** engines: no userns remapping. Container uid 0 = host uid 0 = socket owner (root) — `open()` still succeeds.

## Data representation at the boundary

Unix domain socket carrying the Docker Engine HTTP API (used by both docker and podman's docker-compat listener). saturn's side always speaks this via the `docker` CLI with `DOCKER_HOST=unix:///var/run/docker.sock`.

## Ownership and lifecycle

- The socket's lifecycle is managed by the host (systemd user unit for `podman.socket`, etc.). saturn never creates or destroys it.
- saturn's role is only to bind-mount `$HOST_SOCK` into each container it launches at `/var/run/docker.sock`.
- `$HOST_SOCK` propagates into nested saturn via the `SATURN_HOST_SOCK` env var so the *innermost* saturn still knows the host's path — see [nested-env.md](nested-env.md).

## Constraints per side

### Container side

- Runs as container-root; no sudo is invoked.
- Must not reach for `podman` directly. That bypasses the socket and opens the rootless store — which (if the same rootless store) races with the host's serialized mutations.

### Host side

- Socket must be listening before any `saturn <cmd>` runs. `systemctl --user enable --now podman.socket` is the one-time setup for rootless podman; rootless Docker's equivalent depends on the installer.
- Permission model on the socket file is the engine's concern; saturn does not chmod/chown it.

## Security note

Bind-mounting this socket grants full control of your engine to the container — privileged sibling with `/` mounted is possible. Saturn additionally bind-mounts the projects root (`$HOME/saturn/`) and each selected mixin's paths (credentials like SSH keys and API tokens) path-symmetrically. This is a meaningful blast radius; acceptable for a personal dev tool, not acceptable for untrusted code.

`IS_SANDBOX=1` in the base image tells tools like Claude Code that running as root is intentional — it is not an actual sandbox.
