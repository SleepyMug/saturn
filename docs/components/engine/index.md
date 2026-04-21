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

At import: `os.environ["DOCKER_HOST"] = f"unix://{SATURN_SOCK}"`. `DOCKER_BUILDKIT` defaults to `0` (rootless podman's docker-compat socket doesn't serve BuildKit; harmless on Docker).

### `_run(*args, check=True, capture=False) -> subprocess.CompletedProcess`

Single small wrapper around `subprocess.run` — the only subprocess entry point. `check=True` by default; `capture=True` returns stdout/stderr as text.

### `_find_workspace() -> Path`

Walks cwd upward until a dir contains `.saturn/compose.yaml`. Exits with a `saturn new` hint if it hits `/` with nothing found. See [workspace](../workspace/index.md).

### `_current_container_mounts() -> list[dict]`

Guest-mode helper. Returns the current container's `Mounts` list by calling `docker inspect --format '{{json .Mounts}}' <hostname>` through the bind-mounted socket. The hostname is read via `socket.gethostname()` — a direct syscall that returns the container's hostname (short container id, by default) regardless of env export. `$HOSTNAME` is unreliable because it's a bash shell variable that isn't exported into `docker compose exec` child processes.

Exits with a pointer at `compose.yaml`'s `hostname:` field if the inspect fails — saturn's self-inspect breaks if the user overrides the container hostname.

### `_translate(source: str, mounts: list[dict]) -> str | None`

Given an inside-path source and the current container's mount list, returns the host path backing `source` — or `None` if no mount's destination is an ancestor. Uses longest-match (sorts mounts by descending destination length, picks the first whose destination is an ancestor via `Path.relative_to`). Skips non-bind mounts (named volumes, tmpfs). Returns `str(mount.Source)` when source equals the destination exactly; otherwise `str(mount.Source / rel)`.

### `_translate_compose(compose_yaml: Path, project: str) -> Path`

The pipeline heart. Returns the path to the generated `.saturn/compose.json`.

1. `docker compose -f <compose_yaml> -p <project> config --format json` → spec dict.
2. If **host mode**: write spec verbatim as `.saturn/compose.json`.
3. If **guest mode**:
   a. For each service with a `build:` stanza: `docker build -f <ctx>/<dockerfile> -t <image> <ctx>` (ctx is the inside path compose resolved). On success, `pop` the `build` key from the service. This pre-builds everything before compose sees the spec, and since the resulting spec has only `image:`, compose uses the existing image without re-reading the context.
   b. Call `_current_container_mounts()` once.
   c. For each service's `volumes[]` where `type == "bind"`: `_translate(vol.source, mounts)`. Collect any unresolvable sources. If any → exit with a labelled list; otherwise write the spec as `.saturn/compose.json`.

### `passthrough(argv: list[str]) -> None`

Glue. `_find_workspace()` → `_translate_compose()` → `subprocess.run(["docker", "compose", "-f", <compose.json>, "-p", <project>, *argv])`. On non-zero exit, prints the full command that was run (to stderr) before `sys.exit`ing with the child's returncode.

## Consumed APIs

- [`cmd_base_default`, `cmd_base_build`, `_build_base`](../base-image/index.md#provided-apis) — when argv is `base ...`.
- [workspace discovery](../workspace/index.md#provided-apis) — `_find_workspace`.
- External: the `docker` CLI with the `compose` plugin, talking to the socket at `SATURN_SOCK`.

## Workflows

### Host-mode up

```
cd ~/code/myproj
saturn up -d
```

1. `_find_workspace()` → `/home/guest/code/myproj`; project = `myproj`.
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

1. `_find_workspace()` → `/root/myproj/sub`; project = `sub`.
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
- **Classic builder forced.** `DOCKER_BUILDKIT=0` at import. Harmless on Docker (where BuildKit falls back to classic); mandatory on podman's docker-compat socket.
