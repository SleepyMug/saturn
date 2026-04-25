# Engine pipeline (translate + reverse mount lookup)

> The compose pass-through pipeline: resolve `.saturn/compose.yaml` via `docker compose config --format json`, translate bind-mount sources from inside-paths to host-paths when running in a guest, pre-build any service images (so compose doesn't re-read build contexts), write `.saturn/compose.json`, exec `docker compose -f compose.json -p <basename> <argv>`.

## Overview

saturn itself doesn't parse compose syntax. Every pass-through invocation runs the user's `compose.yaml` through compose's own parser (`compose config --format json`) to get a fully-normalized spec with env vars substituted, relative paths resolved, and short-form volumes expanded to long-form. Saturn then post-processes that spec, writes it back as JSON, and hands off to a second compose invocation that acts on the translated spec.

Two things make this non-trivial inside a saturn container ("guest mode", `SATURN_IN_GUEST=1`):

1. **Bind-mount sources are inside-paths.** `${HOME}/.ssh` substitutes to `/root/.ssh`, but the daemon resolves paths on the host. Saturn asks the daemon for the current container's own mount list via `docker inspect`, then translates every bind source by finding a mount whose destination is an ancestor and mapping source → real host path.
2. **Build contexts are read client-side.** `docker compose` tars the build context from the *client's* filesystem, then streams to the daemon. In guest mode, the client sees inside-paths; the host-translated path doesn't exist locally. Saturn side-steps this by running `docker build` itself (inside-path context works client-side) *before* handing off to compose, then stripping `build:` from the service so compose just uses the already-built image.

Host mode runs the same pipeline but skips translation — the inside and host paths are the same.

## Provided APIs

### Env-derived constants

| Constant | Source | Meaning |
|---|---|---|
| `IS_HOST` | `SATURN_IN_GUEST` != `"1"` | True on host; False inside a saturn-launched container. Gates reverse lookup + build-before-handoff. |
| `SATURN_SOCK` | `$SATURN_SOCK` or computed | Socket path visible to *this* saturn process. Host: first of `$XDG_RUNTIME_DIR/{podman/podman.sock,docker.sock}` or `/var/run/docker.sock`. Guest: `/var/run/docker.sock` (the outer saturn bind-mounts the real host socket here). Always re-exported so `${SATURN_SOCK}` substitutes inside `compose.yaml` at `compose config` time. |
| `BASE_IMAGE` | `$SATURN_BASE_IMAGE` | Base image tag. Default `localhost/saturn-base:latest`. |

At import: `os.environ["DOCKER_HOST"] = f"unix://{SATURN_SOCK}"`. `DOCKER_BUILDKIT` is set adaptively by a pair of engine probes:

1. `_detect_cli()` parses `docker --version` to tell docker-cli from a podman shim (e.g. the `podman-docker` package's `/usr/bin/docker`).
2. `_detect_backend()` runs plain `docker version` through the socket and tests for the substring `"Podman Engine"` in stdout (more robust than `--format '{{json .Server.Components}}'` — the JSON template form fails under podman's own CLI). Also returns whether the socket is root-owned (prior rootful-engine warning, consolidated).

From those:

- **Check A (fail-fast).** `cli == "podman"` with `backend != "podman"` → `sys.exit`. Podman CLI (incl. `--remote`) only speaks podman's native REST API; against a dockerd socket it would fail later with an opaque `ping response was 404`.
- **Check B (adaptive buildkit).** `cli == "docker"` with `backend == "docker"` and the socket not root-owned on host → `os.environ.pop("DOCKER_BUILDKIT")` so docker's default (BuildKit) takes over. Every other combination `setdefault`s `"0"` (classic builder), because podman's docker-compat socket doesn't serve the BuildKit API. The rootful gate only applies on host — inside a guest, rootless-userns mapping shows the socket as root-owned even against a rootless backend.

Opt out with `SATURN_SKIP_ENGINE_PROBE=1`, which skips both probes and keeps `DOCKER_BUILDKIT=0`.

### `_run(*args, check=True, capture=False) -> subprocess.CompletedProcess`

Single small wrapper around `subprocess.run` — the only subprocess entry point. `check=True` by default; `capture=True` returns stdout/stderr as text.

### `find_workspace() -> Path`

Lives in `saturn/workspace.py`; `engine.passthrough` imports it. Walks cwd upward until a dir contains `.saturn/compose.yaml`. Exits with a `saturn new` hint if it hits `/` with nothing found. See [workspace](../workspace/index.md).

### `cmd_host_addr() -> None`

Prints `localhost` (host mode) or `host.docker.internal` (guest mode). One line, no engine calls. Lets scripts use `$(saturn host-addr):PORT` without branching on context.

### `_current_container_mounts() -> list[dict]`

Guest-mode helper. Returns the current container's `Mounts` list by calling `docker inspect --format '{{json .Mounts}}' <hostname>` through the bind-mounted socket. The hostname is read via `socket.gethostname()` — a direct syscall that returns the container's hostname (short container id, by default) regardless of env export. `$HOSTNAME` is unreliable because it's a bash shell variable that isn't exported into `docker compose exec` child processes.

Exits with a pointer at `compose.yaml`'s `hostname:` field if the inspect fails — saturn's self-inspect breaks if the user overrides the container hostname.

### `_translate(source: str, mounts: list[dict]) -> str | None`

Given an inside-path source and the current container's mount list, returns the host path backing `source` — or `None` if no mount's destination is an ancestor. Uses longest-match (sorts mounts by descending destination length, picks the first whose destination is an ancestor via `Path.relative_to`). Skips non-bind mounts (named volumes, tmpfs). Returns `str(mount.Source)` when source equals the destination exactly; otherwise `str(mount.Source / rel)`.

### `_find_overrides(ws: Path) -> list[Path]`

Discovers compose overrides to layer onto `.saturn/compose.yaml`. Two sources, applied in this order:

1. `sorted((ws / ".saturn").glob("compose.override*.yaml"))` — lexically sorted workspace overrides. Matches docker compose's `compose.override.yaml` convention; extended to a glob so `compose.override.local.yaml`, `compose.override.ci.yaml` etc. all participate.
2. `SATURN_COMPOSE_OVERRIDES` env var — colon-separated absolute paths. Empty segments are skipped. The programmatic path used by callers that prefer not to write a file into `.saturn/`.

See [decision 0014](../../decisions/0014-compose-override-chain.md) for rationale.

### `_translate_compose(compose_files: list[Path], project: str) -> Path`

The pipeline heart. Returns the path to the generated `compose.json` next to the first file in the list. Handles a single-file workspace and an arbitrarily long override chain identically — the merge is compose's job.

1. `docker compose -f <f1> -f <f2> … -p <project> config --format json` → merged spec dict. Compose does scalars-replace/lists-append/maps-deep-merge between files; later files layer on top.
2. If **host mode**: write spec verbatim as `.saturn/compose.json`.
3. If **guest mode**:
   a. For each service with a `build:` stanza: `docker build -f <ctx>/<dockerfile> -t <image> <ctx>` (ctx is the inside path compose resolved). On success, `pop` the `build` key from the service. This pre-builds everything before compose sees the spec, and since the resulting spec has only `image:`, compose uses the existing image without re-reading the context.
   b. Call `_current_container_mounts()` once.
   c. For each service's `volumes[]` where `type == "bind"`: `_translate(vol.source, mounts)`. Collect any unresolvable sources. If any → exit with a labelled list; otherwise write the spec as `.saturn/compose.json`.

Reverse mount lookup runs on the *merged* bind sources — a bind declared in an override is resolved the same way as one declared in the base.

### `passthrough(argv: list[str]) -> None`

Glue. `find_workspace()` → `files = [ws/.saturn/compose.yaml, *_find_overrides(ws)]` → `_translate_compose(files, project)` → `subprocess.run(["docker", "compose", "-f", <compose.json>, "-p", <project>, *argv])`. On non-zero exit, prints the full command that was run (to stderr) before `sys.exit`ing with the child's returncode.

### `_run(*args, check=True, capture=False) -> subprocess.CompletedProcess`

Single small wrapper around `subprocess.run`. `_run` is for "we're running a docker command and want to surface failures" — it's used by the engine, the base-image build, and the `docker rmi`/`docker build` paths. It is **not** used by `cmd_docker` (the `saturn docker <args>` shim), which forwards stdio verbatim and propagates the child returncode without raising — see [docker.py](../../decisions/0018-modular-source-zipapp-distribution.md).

## Consumed APIs

- [`cmd_base_default`, `cmd_base_build`, `_build_base`](../base-image/index.md#provided-apis) — when argv is `base ...`.
- [workspace discovery](../workspace/index.md#provided-apis) — `find_workspace`.
- External: the `docker` CLI with the `compose` plugin, talking to the socket at `SATURN_SOCK`.

## Workflows

### Host-mode up

```
cd ~/code/myproj
saturn up -d
```

1. `find_workspace()` → `/home/guest/code/myproj`; project = `myproj`.
2. `_translate_compose()`:
   - `docker compose -f .saturn/compose.yaml -p myproj config --format json` → spec (all paths absolute, all env substituted).
   - Host mode, no translation. Write `.saturn/compose.json`.
3. `docker compose -f .saturn/compose.json -p myproj up -d` — compose builds the workspace image (using `build:` as normal), creates `saturn_myproj`, starts it.

### Guest-mode up (nested)

Inside `saturn_myproj` (cwd `/root/myproj`):

```
mkdir sub && saturn new sub --socket
cd sub && saturn up -d
```

1. `find_workspace()` → `/root/myproj/sub`; project = `sub`.
2. `_translate_compose()`:
   - `docker compose ... config --format json` → spec where, e.g., `services.dev.build.context = /root/myproj/sub/.saturn` and `services.dev.volumes[0].source = /root/myproj/sub`.
   - `_current_container_mounts()` returns the outer container's mounts: `/home/guest/code/myproj:/root/myproj`, `/run/user/1000/podman/podman.sock:/var/run/docker.sock`, etc.
   - Service `dev` has `build:` → `docker build -f /root/myproj/sub/.saturn/Dockerfile -t localhost/saturn-sub:latest /root/myproj/sub/.saturn`. Client reads context from inside-path ✓. Daemon stores image on host engine.
   - Remove `build:` from the service.
   - Translate volume sources: `/root/myproj/sub` → `/home/guest/code/myproj/sub` (via the `/root/myproj` mount); `/var/run/docker.sock` → `/run/user/1000/podman/podman.sock` (via the socket mount).
   - Write `.saturn/compose.json`.
3. `docker compose -f ...compose.json -p sub up -d` — compose sees only `image:`, no rebuild. Daemon starts `saturn_sub` on the host engine with all bind sources as real host paths.

### Fail-fast (unresolvable bind source in guest)

If a bind source doesn't fall under any of the current container's mounts — e.g. `/etc/hostname:/mnt` written into a nested compose.yaml — `_translate` returns `None`. `_translate_compose` collects every such case, exits with:

```
bind-mount source(s) not under any mount of the current container:
  dev.volumes: /etc/hostname
(Inside a saturn container, every compose bind source must live under an existing mount — workspace, socket, or another mounted path.)
```

No compose.json is written; the second `docker compose` never runs.

## Execution-context constraints

- **`hostname:` in compose.yaml breaks self-inspect.** Saturn reads the container's hostname via `socket.gethostname()`; docker/compose set that to the short container id by default. Overriding `hostname:` makes inspect-by-hostname fail — saturn exits with a labelled message.
- **Pre-building in guest requires `image:` to be set.** `docker compose config` auto-fills `image: <project>_<service>` when the user doesn't specify one, so this is normally fine. A service with only `build:` and no explicit `image:` would crash saturn — but compose config's normalization prevents that in practice.
- **Build cache is per-engine.** In guest mode, `docker build` runs against the host engine (via the socket) — the same engine compose will use. Cache hits work normally.
- **Builder selection is adaptive.** `DOCKER_BUILDKIT` is set `"0"` (classic) when the backend probe says podman or when the CLI is podman; unset (docker default, BuildKit on) when both CLI and backend are docker and the host socket is user-owned. Podman's docker-compat socket doesn't serve the BuildKit API, so forcing classic there is mandatory; rootless Docker serves BuildKit fine, so suppressing it was pure regression. See [decision 0016](../../decisions/0016-adaptive-buildkit-and-cli-backend-checks.md).
