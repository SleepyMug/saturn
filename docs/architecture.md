# Architecture

> A `src/saturn/` Python package wrapping `docker compose`, distributed as a single-file zipapp. Saturn's only saturn-specific value-add is the compose-translation pipeline: resolve the user's compose.yaml, reverse-lookup bind-mount paths via engine inspect when running in a guest, and hand off to compose.

## Overview

saturn is a thin wrapper over `docker compose`. Source lives under `src/saturn/` (six modules — `cli`, `env`, `workspace`, `base`, `engine`, `docker` — plus a top-level `__main__.py` for the zipapp entry); it's stdlib-only and assembled into the executable `./saturn` zipapp via `python3 build.py`. The implementation provides:

- a small **seed** command (`new`) that writes `.saturn/Dockerfile` + `.saturn/compose.yaml` from templates keyed by CLI flags (`--ssh`, `--gh`, `--claude`, `--codex`, `--nesting`);
- a **base image** command group (`base default`, `base build <file>`) that builds `localhost/saturn-base:latest`;
- a **compose pass-through** path that takes every unmatched argv, translates the compose spec, and forwards to `docker compose`;
- a **direct-docker pass-through** (`saturn docker <args>`) that forwards verbatim to the `docker` CLI with saturn's resolved `DOCKER_HOST` / `DOCKER_BUILDKIT` already set — for callers that want to drive the engine without compose.

The translation pipeline is the core idea. Host-mode is a near-no-op (compose config → json → forward). Guest-mode (inside a saturn container) does two extra things: (a) pre-build any service that declares `build:` so compose doesn't try to re-read the inside-path context on the host; (b) translate every bind-mount source from inside-path to host-path using the current container's own `.Mounts` list (fetched via `docker inspect` through the bind-mounted socket).

The zipapp is one file on disk, distributed as-is (`curl | chmod +x`). The base image embeds the same zipapp at `/usr/local/bin/saturn`, so running saturn inside a saturn container is just saturn. (The `materialize_script` helper handles both invocation forms when assembling the base build context — copies `sys.argv[0]` for a zipapp invocation; otherwise builds a fresh zipapp from `src/`.)

## Logical layering

```
┌──────────────────────────── cli ────────────────────────────┐
│   main() routes sys.argv[1] (after probe_engine()):          │
│     "new"     → cmd_new      (argparse flag parsing)        │
│     "base"    → cmd_base_*   (argparse sub-dispatch)        │
│     "docker"  → cmd_docker   (verbatim docker CLI shim)     │
│     "shell"   → rewrite argv to "exec dev bash", fall thru  │
│     "host-addr" → cmd_host_addr                              │
│     else      → passthrough(argv)                            │
└──┬────────┬───────────┬───────────┬───────────────┬─────────┘
   │        │           │           │               │
   ▼        ▼           ▼           ▼               ▼
┌────┐ ┌─────────┐ ┌──────────┐ ┌──────┐  ┌────────────────┐
│ env│ │workspace│ │base image│ │docker│  │     engine     │
│env-│ │(seeding │ │(build /  │ │(thin │  │(translate +    │
│derive│ templates│ inlined   │ │ shim │  │ pass-through   │
│vars +│+ find ) │ Dockerfile)│ │ over │  │ pipeline)      │
│probe)│         │            │ │docker│  │                │
└──┬─┘ └───┬─────┘ └────┬─────┘ └──┬───┘  └──────┬─────────┘
   │       │            │          │             │
   │       │            │          ▼             ▼
   │       │            │     ┌────────┐  ┌────────────┐
   │       │            │     │ docker │  │ subprocess │
   │       │            └─────┤  CLI   │  │   docker   │
   │       └──────────────────┤        │  │   compose  │
   └──────────────────────────┤        │  │  (+ build) │
                              └───┬────┘  └─────┬──────┘
                                  ▼             ▼
                       DOCKER_HOST → host engine socket
```

- **cli** ([components/cli](components/cli/index.md)) — `main()` is a small switch on `sys.argv[1]` + argparse only where flags exist. Calls `env.probe_engine()` once before dispatch.
- **env** — module-level constants (`IS_HOST`, `SATURN_SOCK`, `BASE_IMAGE`) read from environment at import; `probe_engine()` runs the cli/backend detection (gated, so unit tests don't hit `docker --version`).
- **workspace** ([components/workspace](components/workspace/index.md)) — the `.saturn/` directory. `cmd_new` seeds templates; `find_workspace` walks cwd upward to find `.saturn/compose.yaml` for pass-through commands.
- **base image** ([components/base-image](components/base-image/index.md)) — inlined minimal Dockerfile (Debian + docker-cli + compose-plugin + python3/git/curl); temp-dir build context with a single-file saturn binary materialized via `materialize_script`.
- **engine** ([components/engine](components/engine/index.md)) — the translation pipeline. `_current_container_mounts`, `_translate`, `_translate_compose`, `passthrough`. The only code path that touches subprocess except the base-image helpers and `cmd_docker`.
- **docker** — `cmd_docker(argv)`: a few lines that `subprocess.run(["docker", *argv])` and propagate the returncode. No translation, no compose.

## Key data flows

### `saturn new [dir] [--flags]`

1. `target = Path(arg or ".").resolve()`; `mkdir -p` if missing; validate basename.
2. `mkdir -p <target>/.saturn`; write `Dockerfile` and `compose.yaml` (with per-flag install lines / bind-mount lines). Never overwrite.
3. Host mode only: for each selected `--ssh` / `--gh` / `--claude` / `--codex`, auto-create the host-side source path so `up` won't fail the bind mount.

Pure filesystem; no engine calls. Nested `saturn new ./sub` works because the current workspace is already bind-mounted.

### `saturn up -d` (or any pass-through)

1. `ws = find_workspace()` (walk cwd upward). `project = ws.name`.
2. `files = [ws/.saturn/compose.yaml, *_find_overrides(ws)]` — the committed base plus any `.saturn/compose.override*.yaml` and `SATURN_COMPOSE_OVERRIDES` entries ([decision 0014](decisions/0014-compose-override-chain.md)).
3. `compose_json = _translate_compose(files, project)`:
   - Run `docker compose -f f1 -f f2 … -p <project> config --format json` → merged, env-substituted, path-normalized spec with volumes in long-form. Compose does the `-f` merge itself (scalars replace, lists append, maps deep-merge).
   - Host mode: write spec → `compose.json`. Done.
   - Guest mode:
     - For each service with `build:`: `docker build -f <ctx>/<dockerfile> -t <image> <ctx>` — client reads context via inside-path, daemon stores result on host engine. Strip `build:` from the service.
     - Get current container's `.Mounts` via `docker inspect $(gethostname)`.
     - For each bind-type volume (from base or override — merge already happened): find the mount whose destination is the longest ancestor of `vol.source`, replace `vol.source` with `mount.Source + rel`. Collect unresolvables; if any → fail-fast.
     - Write translated spec → `compose.json`.
4. `subprocess.run(["docker", "compose", "-f", compose_json, "-p", project, *argv])`. On non-zero exit, print the command that ran before propagating the returncode.

### `saturn base default`

1. `docker rmi BASE_IMAGE` (ignored if absent).
2. `TemporaryDirectory`, write inlined `BASE_DOCKERFILE`, `shutil.copy(SCRIPT, tmp/saturn)`.
3. `docker build -f tmp/Dockerfile -t BASE_IMAGE tmp`.

`saturn base build <file>` is the same with a user-supplied Dockerfile.

### Nesting (host vs guest unification)

The same `compose.yaml` template is valid in both modes because env substitution (`${HOME}`, `${SATURN_SOCK}`) naturally picks up the right value in each context:

- Host: `${HOME}` = `/home/guest`; `${SATURN_SOCK}` = `/run/user/1000/podman/podman.sock`. No translation needed.
- Guest: `${HOME}` = `/root`; `${SATURN_SOCK}` = `/var/run/docker.sock`. Reverse lookup maps those inside paths back to the real host paths using the current container's mount list.

Inside a saturn container (`SATURN_IN_GUEST=1`), running `saturn up` for any subdirectory of the current workspace just works — the workspace was bind-mounted by the outer saturn, so the inside path for the sub-workspace is already under a known mount. `saturn up /some/other/path` fails fast in `_translate_compose` when the bind source `/some/other/path` has no ancestor among the current container's mounts.

## Execution-context constraints

- **No daemon; no state.** Every `saturn <cmd>` is a fresh process. State lives in engine objects (images, containers, networks) and on-disk files (`compose.yaml`, `compose.json`). `compose.json` is a regenerated derivative — delete it at will.
- **stdlib only.** argparse, subprocess, pathlib, shutil, tempfile, os, sys, json, socket, zipapp. No third-party Python deps.
- **Source vs. distribution.** Code is edited under `src/saturn/`; the `./saturn` zipapp at repo root is a build artifact rebuilt by `python3 build.py`. The zipapp is what `curl` installs and what the base image's `COPY saturn /usr/local/bin/saturn` step copies in.
- **Hard dep on `docker` CLI with `compose` plugin.** Saturn shells out to `docker compose` for parsing and execution; there's no fallback parser. Debian's `docker-compose` package provides the plugin; so does `docker.io` on most distros.
- **Rootless engine strongly preferred.** Running as container-root works under any engine; the ownership ergonomics (files on disk owned by host-you) depend on rootless userns remapping.
- **Adaptive builder selection.** `DOCKER_BUILDKIT` is set at import based on a pair of probes: docker-cli + docker backend (rootless, on host or guest) → unset, docker's BuildKit default wins. Any podman involvement → `"0"` (classic). Podman-CLI × docker-backend fails fast. See [engine/index.md](components/engine/index.md#env-derived-constants) and [decision 0016](decisions/0016-adaptive-buildkit-and-cli-backend-checks.md).
