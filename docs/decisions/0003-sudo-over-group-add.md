# 0003 — Unified rootless/sudo recipe; one CLI; socket serialization

> **Superseded by [0007](0007-drop-agent-user-use-is-sandbox.md).** Containers now run as root, so no sudo is needed. The socket-access story simplifies: container-uid 0 maps to host-user under rootless userns and can open the socket directly. The "one CLI, always `docker`" and "don't shell to `podman` directly" rules remain in force.

> Inside saturn containers, always `sudo docker ...`. This single recipe covers rootless podman, rootless Docker, rootful docker, and rootful podman. saturn never shells to `podman` directly.

## Context

Initial exploration enumerated three host families for nested container control via the engine socket: rootful docker (clean `--group-add <gid>` path), rootful podman (same shape), and rootless podman (fundamentally different — the socket is owned by the host user, which maps to container uid 0 under user namespaces; `--group-add` doesn't help because the socket's group maps to container gid 0).

A two-recipe split (docker-group on rootful, userns-keep-id on rootless podman) was considered. But `sudo` inside the container works on every family:

- Rootless podman/Docker: inside-uid 0 ↔ host uid 1000 via userns = socket owner → permission passes.
- Rootful docker/podman: inside-uid 0 = host uid 0 = socket owner (root) → permission passes.

## Decision

- Every engine call inside a saturn container is `sudo docker <args>`, controlled by the `SATURN_SUDO=1` env var injected by `up`/`project shell`.
- saturn always invokes `docker`, never `podman`, at all nesting levels. The docker CLI speaks podman's docker-compat API via `DOCKER_HOST=unix://...`, so one CLI works for both engine families.
- The `SATURN_ENGINE=podman|docker` env var only selects the default *socket path* (`/run/user/<uid>/podman/podman.sock` vs `.../docker.sock`); it does not select a CLI.
- `DOCKER_BUILDKIT=0` forced because podman's docker-compat socket doesn't serve the BuildKit API.

## Consequences

- One runtime recipe end to end. No `--group-add`, no `--userns=keep-id`, no per-engine branching.
- Target hosts are **rootless engines** (rootless podman, Rootless Docker). Rootful engines still work because sudo lands on root=socket-owner, but they're not first-class targets.
- `NOPASSWD: ALL` sudoers entry for `agent` in the base image. Security-wise identical to granting the socket (which is already full engine access); sudo just makes that reality visible rather than hidden behind group-membership shenanigans.
- `podman` calls from inside saturn are forbidden — a rootless podman CLI opens the store directly and races with the host's serialized mutations. See the README section "Avoiding podman storage races".

## Rejected alternatives

- **`--group-add $(stat -c %g $SOCK)`** — clean for rootful engines; breaks on rootless because the socket's group maps to container gid 0.
- **`--userns=keep-id` + `--user 10001:10001`** — aligns container uid with host uid so the socket just works, but it's podman-only; diverges from "portable single recipe."
- **`userns-remap` on rootful docker** — doesn't actually unify recipes (socket stays `root:docker` regardless of container process remapping). See the exploration in the session history.
