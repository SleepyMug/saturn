# Base image

> Minimal Debian-trixie-slim base with `docker` CLI + `compose` plugin, `python3`, `git`, `curl`, and the saturn script itself. Workspace Dockerfiles `FROM` it and add their own tooling. Inlined Dockerfile; build context is assembled in a tempdir with a copy of saturn.

## Overview

`localhost/saturn-base:latest` is the parent image for every workspace container. It's deliberately minimal: saturn no longer carries a mixin registry, so per-tool install steps (ssh client, gh, nodejs+claude, nodejs+codex) live in each workspace's own `.saturn/Dockerfile` (seeded by `saturn new --<flag>`).

Contract:

- Debian trixie slim.
- `docker-cli` and `docker-compose` packages (provides the compose v2 plugin as a docker CLI subcommand).
- `ca-certificates`, `python3`, `git`, `curl`.
- `/usr/local/bin/saturn` present and executable (so nesting works).
- `ENV IS_SANDBOX=1` (Claude Code and similar tools use this as a marker that root is intentional; they refuse to run as root without it).
- `CMD ["sleep", "infinity"]` (workspace compose files also set this; belt-and-braces).
- Runs as root. No user creation, no `USER` directive. Rootless userns remaps to the invoking host user; sudo is unnecessary.

Distribution is one file: `curl .../saturn -o ~/.local/bin/saturn && chmod +x`.

## Provided APIs

### `BASE_IMAGE: str`

`os.environ.get("SATURN_BASE_IMAGE", "localhost/saturn-base:latest")`. Override the tag via env.

### `BASE_DOCKERFILE: str`

The full inlined Dockerfile (no templating, no splicing). Shape:

```dockerfile
FROM docker.io/library/debian:trixie-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      docker-cli docker-compose ca-certificates python3 git curl \
 && rm -rf /var/lib/apt/lists/*

COPY saturn /usr/local/bin/saturn
RUN chmod 0755 /usr/local/bin/saturn

ENV IS_SANDBOX=1

CMD ["sleep", "infinity"]
```

### `_build_base(dockerfile_text: str) -> None`

Shared helper for `cmd_base_default` and `cmd_base_build`.

1. `tempfile.TemporaryDirectory(prefix="saturn-base-")` — auto-cleanup.
2. Write `dockerfile_text` into `<tmp>/Dockerfile`.
3. `materialize_script(<tmp>/saturn)` — drops a single-file saturn binary at the path, regardless of how saturn was invoked. (Zipapp: `shutil.copy(sys.argv[0], dst)`. Source: `zipapp.create_archive(src/, dst)`.) The base image's `COPY saturn /usr/local/bin/saturn` step then copies it in.
4. `docker build -f <tmp>/Dockerfile -t BASE_IMAGE <tmp>`.

### `materialize_script(dest: Path) -> None`

Places an executable single-file saturn binary at `dest`.

- **Zipapp invocation** (the normal case — `./saturn base default` from a zipapp on disk): `Path(sys.argv[0]).resolve()` is a real file → `shutil.copy(argv0, dest)` and `chmod 0755`.
- **Source invocation** (`python -m saturn` from a checkout): `sys.argv[0]` points at `src/saturn/__main__.py`, not a single file. Build a fresh zipapp from `src/` (which contains the top-level `__main__.py` and the `saturn/` package) into `dest` via `zipapp.create_archive(...)`, then `chmod 0755`.

Either way, the next `docker build` step in `_build_base` finds a single executable `saturn` at the build-context root.

### `cmd_base_default(args) -> None`

Force-rebuild from the inlined default. `docker rmi BASE_IMAGE` (ignored if absent, quietly) → `_build_base(BASE_DOCKERFILE)`.

### `cmd_base_build(args) -> None`

Force-rebuild from a user-supplied Dockerfile. Errors if the file is missing. The file is used verbatim — it must keep `COPY saturn /usr/local/bin/saturn` or nesting breaks.

## Consumed APIs

- `_run` subprocess wrapper.
- `BASE_IMAGE` from `env`.
- `sys.argv[0]` and `Path(__file__)` for invocation-form detection inside `materialize_script`.

## Workflows

### Fresh host first build

```
saturn base default
```

1. `_run("docker", "rmi", BASE_IMAGE, check=False, capture=True)` — silent no-op if absent.
2. `_build_base(BASE_DOCKERFILE)` as above.
3. Image is in the host engine's local store, tagged `localhost/saturn-base:latest`.

### Custom base

```
# Write your own Dockerfile (must COPY saturn):
cat > my-base.Dockerfile <<'EOF'
FROM docker.io/library/debian:trixie-slim
RUN apt-get update && apt-get install -y docker-cli docker-compose python3 git curl vim htop && rm -rf /var/lib/apt/lists/*
COPY saturn /usr/local/bin/saturn
RUN chmod 0755 /usr/local/bin/saturn
ENV IS_SANDBOX=1
CMD ["sleep", "infinity"]
EOF

saturn base build my-base.Dockerfile
```

### Workspace Dockerfiles

Seeded by `saturn new --<flag>` (see [workspace](../workspace/index.md#provided-apis) for the per-flag install blocks). The pattern is:

```dockerfile
FROM localhost/saturn-base:latest

# ...per-flag install RUN lines, if any...
```

Workspaces without any flags get just the `FROM` line — they pick up everything the base has and nothing extra.

## Execution-context constraints

- **No `base template` command.** Previous designs emitted a rendered Containerfile for editing. The new base image doesn't splice anything, so `cat`-ing the inlined string (or the actual file if you want a copy) is equivalent. Use `saturn base build <your-file>` when you want a custom.
- **COPY ordering matters.** `COPY saturn` must come after the `apt-get install` layer so `chmod 0755` has `/usr/local/bin` available. The inlined Dockerfile enforces this.
- **Base image doesn't carry tool-specific installs.** SSH client, gh, claude, nodejs+codex all live in per-workspace Dockerfiles now — inserted by `saturn new --ssh`, `--gh`, `--claude`, `--codex`. Re-running `saturn base default` does not re-install any of those; edit the workspace Dockerfile if you want them.
- **Inside the container, the saturn binary is the same zipapp as on host.** `materialize_script` produces a byte-identical zipapp (or copy of one); nesting works because the saturn at `/usr/local/bin/saturn` inside is the same code that built the image.
