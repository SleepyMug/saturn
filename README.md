# saturn

## What it is

Single-file Python wrapper over `docker compose` for rootless podman / rootless Docker. A **workspace** is any directory with a `.saturn/compose.yaml` in it. Saturn passes the compose spec through a small translation step and forwards to `docker compose` — so `saturn up`, `saturn down`, `saturn shell`, `saturn logs -f`, `saturn ps` all just work. The translation step pre-resolves env vars + paths and, inside a saturn container, rewrites bind-mount sources from inside paths to the real host paths by inspecting the current container through the host engine socket.

The container runs as root; rootless userns maps that to your host user, so files written from inside land on disk with host-user ownership. Works at any nesting level (pass `--socket` to `saturn new` on the outer workspace — see flags below).

## What it solves

- **Nested containers without a registry.** The novel bit: inside a saturn container, `docker inspect $(hostname)` returns the current container's `.Mounts` list, which saturn uses as a ground-truth map from inside-paths to host-paths. That's how `saturn up` for a subdirectory of the current workspace "just works" — no propagated per-workspace env vars, no bookkeeping layer. Sibling containers launched from inside run on the host engine, transparently.
- **No custom config schema.** `compose.yaml` is the source of truth. If you know compose, you know saturn — the whole tool is "translate + hand off to `docker compose`".
- **Root inside the container is the right default.** Rootless userns remapping turns container-uid-0 into your host user at the bind-mount boundary, so files written from inside land on disk owned by you. No uid-matching build args, no chown-on-mount dance.

## Conventions

Nested rootless container usage is the underlying problem, and the reason saturn can be this thin is a small set of conventions that carry the weight. Saturn (the script) implements what we have by assuming them — any container built to satisfy them can host saturn inside.

- **Workspace = a directory with `.saturn/compose.yaml`.** Discovered by walking cwd upward; no registry, no global index.
- **`compose.yaml` is the source of truth.** Saturn never parses compose-shaped input itself; it post-processes `docker compose config --format json`.
- **Host engine socket bind-mounted at `/var/run/docker.sock`.** The single boundary crossing — everything saturn learns about the outside world comes through it.
- **Two-var env contract: `SATURN_IN_GUEST=1` and `SATURN_SOCK=/var/run/docker.sock`.** Static workspace-level config; no per-launch data. Previous versions propagated one env var per mixin; all gone.
- **Default container hostname.** `docker inspect $(hostname)` is saturn's self-identity; don't override `hostname:` in a workspace `compose.yaml`.
- **Reverse mount lookup as the translation primitive.** Inside a guest, `.Mounts` from self-inspect is the ground-truth map from inside-paths to host-paths — one generic lookup subsumes every host-path env var saturn used to need.

The detailed spec — image requirements, the exact bind-mount list, env var semantics — lives in [API saturn consumes](#api-saturn-consumes). A container meeting that spec can run saturn, whether saturn seeded it or not.

## Prerequisites

- **Rootless podman** (with `systemctl --user enable --now podman.socket`) **or rootless Docker**. Rootful engines work technically but produce host-root-owned files on bind mounts.
- **`docker` CLI with the `compose` plugin** on your `$PATH` (Debian: `docker-cli` + `docker-compose` packages).
- **The `saturn` script** on `$PATH`:
  ```sh
  curl -fsSL <url>/saturn -o ~/.local/bin/saturn && chmod +x ~/.local/bin/saturn
  ```
- **The base image built once** — `saturn base default` produces `localhost/saturn-base:latest` (Debian trixie + `docker-cli` + `docker-compose` plugin + `python3` + `git` + `curl` + saturn baked in at `/usr/local/bin/saturn`).

## Typical workflow

```sh
# one-time
saturn base default

# create a workspace
saturn new ~/code/myproj --ssh --claude --socket   # seeds templates; auto-creates ~/.ssh, ~/.claude, ~/.claude.json
$EDITOR ~/code/myproj/.saturn/Dockerfile           # optional: add more tooling
cd ~/code/myproj && saturn up -d                   # build workspace image + start container

# day-to-day
saturn shell                                       # interactive bash in the `dev` service
saturn exec dev make test
saturn logs -f dev
saturn down
```

Always use `saturn up -d`. Bare `saturn up` attaches to the `dev` service, whose command is `sleep infinity` — you'll see no output, and Ctrl+C stops the container. Saturn prints a reminder if you forget.

Saturn discovers the workspace by walking `cwd` upward for `.saturn/compose.yaml`, so the day-to-day commands work from any subdirectory. Inside the container you start in `/root/<basename>` — the host workspace dir, bind-mounted. To see running saturn containers from the host:

```sh
docker ps --filter name=saturn_
```

### Nested

With `--socket` on the outer workspace, `saturn` inside can launch a sibling for any subdirectory of the current workspace:

```sh
# inside ~/code/myproj's container (cwd = /root/myproj):
mkdir sub
saturn new sub --ssh --socket
cd sub
saturn up -d           # saturn_sub runs on the host engine
```

Saturn asks the host engine for the current container's bind mounts (`docker inspect <self>`), finds that `/root/myproj` maps to `/home/guest/code/myproj` on host, and rewrites the child compose's `..:/root/sub` into `/home/guest/code/myproj/sub:/root/sub` before handing off. The same translation handles `${HOME}/.ssh` → `/root/.ssh` → (on host) `/home/guest/.ssh`, and `${SATURN_SOCK}` → `/var/run/docker.sock` → (on host) `/run/user/1000/podman/podman.sock`.

Bind-mount sources outside the current container's mounts fail fast with a labelled list:

```
bind-mount source(s) not under any mount of the current container:
  dev.volumes: /etc/hostname
(Inside a saturn container, every compose bind source must live
under an existing mount — workspace, socket, or another mounted path.)
```

## API saturn provides

### CLI commands

| Command | What it does |
|---|---|
| `saturn new [dir] [flags]` | Seed `<dir>/.saturn/{Dockerfile,compose.yaml}`. Default `dir`=cwd. |
| `saturn base default` | Rebuild `localhost/saturn-base:latest` from the inlined minimal Dockerfile. |
| `saturn base build <file>` | Rebuild the base from a user-supplied Dockerfile. |
| `saturn shell` | Alias for `saturn exec dev bash`. |
| `saturn <anything else>` | Forwarded to `docker compose -f .saturn/compose.json -p <workspace> <args>`. |

Semantics:

- `saturn new` is idempotent: never overwrites existing `.saturn/Dockerfile` or `compose.yaml`. In host mode, it auto-creates any missing host-side bind-mount sources (e.g. `~/.ssh`, `~/.claude.json`) so the first `saturn up` doesn't fail.
- `saturn base {default,build}` runs `docker rmi` on the existing tag, then rebuilds. The tag can be overridden with `SATURN_BASE_IMAGE`.
- Pass-through covers the full compose surface: `up`, `up -d`, `down`, `exec dev <cmd>`, `logs -f`, `ps`, `restart dev`, `build`, and so on. The translated spec is written to `.saturn/compose.json` each invocation.

### `saturn new` flags

If none of `--ssh`/`--gh`/`--claude`/`--codex` is passed, `saturn new` defaults to `--ssh --gh --claude`. `--socket` is independent.

| Flag | Adds to `Dockerfile` | Adds to `compose.yaml` volumes |
|---|---|---|
| `--ssh` | `apt install openssh-client` | `${HOME}/.ssh:/root/.ssh` |
| `--gh` | `apt install gh` | `${HOME}/.config/gh:/root/.config/gh` |
| `--claude` | `curl -fsSL https://claude.ai/install.sh \| bash` | `${HOME}/.claude:/root/.claude` + `${HOME}/.claude.json:/root/.claude.json` |
| `--codex` | `apt install nodejs npm` + `npm i -g @openai/codex` | `${HOME}/.codex:/root/.codex` |
| `--socket` | (none) | `${SATURN_SOCK}:/var/run/docker.sock` — required if you want nested `saturn` or plain `docker` to work inside the container |

### Files written

```
<workspace>/
  .saturn/
    Dockerfile     # seeded once, user edits; FROM localhost/saturn-base:latest + tooling
    compose.yaml   # seeded once, user edits; source of truth
    compose.json   # regenerated every saturn invocation; treat as a derivative
```

### Exit codes and banners

- Saturn forwards the child `docker compose` returncode as its own exit code.
- On non-zero exit, saturn prints a `(docker compose exited N — ran: ...)` banner on stderr to flag non-obvious compose failures.
- Banner is suppressed for `exec`/`run` (where the returncode is the inner command's status — e.g. bash's last exit code on Ctrl-D out of `saturn shell`) and for 130 (user-initiated SIGINT).
- Bare `saturn up` prints a stderr reminder suggesting `-d`, because attaching to `sleep infinity` is rarely what you want.

### Naming

Container: `saturn_<basename>`. Image: `localhost/saturn-<basename>:latest`. Compose project: `<basename>`. `<basename>` is the dir's final path component normalized to the compose project-name regex (`^[a-z0-9][a-z0-9_-]*$`): lowercased, with any other char replaced by `-`. So `/home/user/MyProj` → `myproj`; `~/code/weird.name @2` → `weird-name-2`. The dir on disk keeps its original name; saturn prints a note whenever normalization changes anything. Two workspaces that normalize to the same basename collide at `up` time (docker's "container name in use") — rename a dir to disambiguate.

## API saturn consumes

### Engine contract

- **`docker` CLI with `compose` plugin** on `$PATH`. Saturn shells out to `docker compose` for parsing and execution; there is no fallback parser.
- **A Unix socket** at `$SATURN_SOCK`. Auto-picked at import: first of `$XDG_RUNTIME_DIR/podman/podman.sock`, `$XDG_RUNTIME_DIR/docker.sock`, `/var/run/docker.sock` that exists. Exported back as `DOCKER_HOST=unix://$SATURN_SOCK`.
- **`docker inspect $(hostname)` returns a `.Mounts` list** (guest mode only). Reverse-lookup depends on the container's hostname matching what the engine knows; `socket.gethostname()` is what saturn asks for.
- **Classic builder**. Saturn sets `DOCKER_BUILDKIT=0` at import because podman's docker-compat socket doesn't serve the BuildKit API. Harmless on Docker (falls back to classic).

### Host environment variables

Saturn reads (and in some cases re-exports) these at import:

| Var | Meaning |
|---|---|
| `SATURN_SOCK` | Explicit socket path. Overrides auto-detection. Exported back as `DOCKER_HOST=unix://$SATURN_SOCK` and substituted into `${SATURN_SOCK}` in workspace `compose.yaml`. |
| `SATURN_BASE_IMAGE` | Override the base image tag (default `localhost/saturn-base:latest`). |
| `XDG_RUNTIME_DIR`, `HOME` | Fallbacks for socket auto-detection and for `${HOME}` substitutions in compose templates. |
| `SATURN_IN_GUEST` | **Must not be set on host.** Presence flips saturn into guest mode and breaks self-inspect. If it leaked from a `saturn shell` session, unset it or restart your shell. |

### Inside-container contract

For a container to run saturn successfully (whether saturn-seeded or your own launcher), it must satisfy:

**Image requirements**:
- `python3` (stdlib-only; no third-party deps)
- `docker` CLI with the `compose` plugin (Debian: `docker-cli` + `docker-compose` packages)
- `/usr/local/bin/saturn` (the script, mode 0755)

The shipped `saturn-base` satisfies all three. Workspace images inherit via `FROM localhost/saturn-base:latest`.

**Runtime bind mounts**:
- Host engine socket at `/var/run/docker.sock`. Required for saturn inside to run `docker compose` and self-inspect.
- The workspace's host dir at `/root/<basename>`. Required so saturn's cwd-upward walk finds `.saturn/compose.yaml`.
- Anything else the workspace's `compose.yaml` declares.

**Environment** (written into the seeded workspace `compose.yaml`'s `environment:` block by `saturn new`; static workspace-level config, not per-launch):

| Var | Value inside | Role |
|---|---|---|
| `SATURN_IN_GUEST` | `"1"` | Flips `IS_HOST=False`. Enables reverse mount lookup + guest-mode pre-build-then-strip. Without it, saturn does a naive pass-through with no translation. |
| `SATURN_SOCK` | `/var/run/docker.sock` | Sets `DOCKER_HOST=unix://$SATURN_SOCK` at import. Re-exported so `${SATURN_SOCK}` substitutes inside the workspace compose.yaml. |

**Container hostname** must match what the engine knows. Docker/compose default the hostname to the short container id. **Do not override `hostname:` in the workspace compose.yaml** — saturn's self-inspect will fail.

## Optional read

### Prebaking tools into the base

Every workspace `.saturn/Dockerfile` seeded by `saturn new --<flag>` inherits from `saturn-base` and re-runs its own `apt install` / `curl | bash` step. Docker's layer cache shares those layers across workspaces that declare identical `RUN` lines, so the second workspace you build with the same flags is near-instant. If you'd rather skip the per-workspace install step entirely — or bake tools not covered by the shipped flags — write your own base Dockerfile, build it once, and point workspaces at it via `SATURN_BASE_IMAGE`.

```dockerfile
# my-base.Dockerfile
FROM docker.io/library/debian:trixie-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      docker-cli docker-compose ca-certificates python3 git curl \
      openssh-client gh \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://claude.ai/install.sh | bash

COPY saturn /usr/local/bin/saturn
RUN chmod 0755 /usr/local/bin/saturn

ENV IS_SANDBOX=1
ENV PATH=/root/.local/bin:${PATH}
CMD ["sleep", "infinity"]
```

```sh
saturn base build my-base.Dockerfile
export SATURN_BASE_IMAGE=localhost/saturn-base:latest  # or a custom tag
saturn new ~/code/myproj                               # workspace Dockerfile FROMs your base
```

Each `saturn new --<flag>` does two things: appends an install step to the workspace `Dockerfile`, and appends a bind-mount to `compose.yaml`. Prebaking replaces only the first. If your custom base already has (say) `gh` installed, dropping `--gh` from `saturn new` avoids re-running the install, but you must still add `- ${HOME}/.config/gh:/root/.config/gh` to the workspace's `compose.yaml` by hand — otherwise `gh` inside the container will have no auth config. Same pattern for `ssh`, `claude`, `codex`.

### Avoiding podman storage races

Rootless podman has no always-on daemon. Every `podman` CLI invocation opens `~/.local/share/containers/storage/` directly and mutates it. **Concurrent invocations race and can corrupt the store.**

The fix: route every operation through the user-level podman API service, which serializes mutations.

```sh
# one-time
systemctl --user enable --now podman.socket
```

Saturn itself already sets `DOCKER_HOST` (to the first of `$XDG_RUNTIME_DIR/podman/podman.sock`, `$XDG_RUNTIME_DIR/docker.sock`, `/var/run/docker.sock` that exists) and `DOCKER_BUILDKIT=0` at import, so `saturn <anything>` funnels through the socket automatically. The exports below are only for when you invoke `docker` yourself, outside saturn, from the same shell:

```sh
# ~/.bashrc — only needed for bare `docker` commands outside saturn
export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock
export DOCKER_BUILDKIT=0
```

Always use `docker` instead of `podman`. `DOCKER_BUILDKIT=0` is required because podman's docker-compat socket doesn't serve the BuildKit API.

**Don't mix.** Every direct `podman` invocation bypasses the serializer and reintroduces the race.

### How it works

**Seeded `compose.yaml` shape**:

```yaml
services:
  dev:
    build:
      context: .
      dockerfile: Dockerfile
    image: localhost/saturn-<name>:latest
    container_name: saturn_<name>
    init: true
    working_dir: /root/<name>
    command: ["sleep", "infinity"]
    environment:
      SATURN_IN_GUEST: "1"
      SATURN_SOCK: /var/run/docker.sock
    volumes:
      - ..:/root/<name>
      # plus one line per --<flag>
```

`${HOME}` and `${SATURN_SOCK}` are compose env substitutions. On host, they resolve to the user's real home and host socket; in guest, they resolve to `/root` and `/var/run/docker.sock` (inside paths), which are then reverse-looked-up by saturn. **One compose.yaml, both modes.**

**The translation pipeline** — every pass-through command goes through:

1. `docker compose -f .saturn/compose.yaml -p <workspace> config --format json` — compose parses and normalizes the spec (env substitution, relative paths → absolute, short-form volumes → long-form).
2. **Host mode**: the spec is written verbatim to `.saturn/compose.json`.
3. **Guest mode** (`SATURN_IN_GUEST=1` set inside a saturn container):
   - For each service with a `build:` stanza, saturn runs `docker build` itself (client reads context via inside path; daemon stores image on host engine), then strips `build:` from the service.
   - Saturn asks the host engine for its own container's `.Mounts` list via `docker inspect $(socket.gethostname())` and translates every bind-mount source: find the longest mount whose destination is an ancestor of the source, replace the source with the mount's host path + relative suffix. Unresolvable sources → fail fast.
   - Translated spec → `.saturn/compose.json`.
4. `docker compose -f .saturn/compose.json -p <workspace> <original argv>`.

**The base image**: Debian trixie + `docker-cli` + `docker-compose` (compose v2 plugin) + `python3` + `git` + `curl` + `saturn` at `/usr/local/bin/saturn` + `ENV IS_SANDBOX=1`. No ssh/gh/nodejs/claude/codex — those move into per-workspace Dockerfiles when you pass `--ssh`, `--gh`, `--claude`, `--codex` to `saturn new`.

### Running saturn in a custom container

`saturn up` produces containers that satisfy the [inside-container contract](#inside-container-contract) automatically. If you're writing your own launcher or image, a minimal working setup looks like:

```sh
docker run -d --init --name my-container \
  -v /run/user/1000/podman/podman.sock:/var/run/docker.sock \
  -v /path/to/ws:/root/ws \
  -v $HOME/.ssh:/root/.ssh \
  -e SATURN_IN_GUEST=1 \
  -e SATURN_SOCK=/var/run/docker.sock \
  -w /root/ws \
  localhost/saturn-base:latest

# inside:
docker exec -it my-container bash
cd /root/ws
saturn up -d
```

### Security note

Saturn bind-mounts whatever your workspace `compose.yaml` declares. With `saturn new --socket` the list is: host engine socket (full engine control), workspace dir, plus any credential paths from `--ssh/--gh/--claude/--codex`. This is a meaningful blast radius; acceptable for a personal dev tool, not for untrusted code. `IS_SANDBOX=1` is a tool-level affirmation that root is intentional, not an actual sandbox.
