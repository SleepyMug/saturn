# saturn

Single-file Python wrapper over `docker compose` for rootless podman / rootless Docker. A **workspace** is any directory with a `.saturn/compose.yaml` in it. Saturn passes the compose spec through a small translation step and forwards to `docker compose` — so `saturn up`, `saturn down`, `saturn exec dev bash`, `saturn logs -f`, `saturn ps` all just work. The translation step pre-resolves env vars + paths and, inside a saturn container, rewrites bind-mount sources from inside paths to the real host paths by inspecting the current container through the host engine socket.

The container runs as root; rootless userns maps that to your host user, so files written from inside land on disk with host-user ownership. Works at any nesting level.

## Install

Single file. `saturn base default` builds `localhost/saturn-base:latest` from an inlined Dockerfile (Debian trixie + `docker-cli` + `docker-compose` plugin + `python3` + `git` + `curl`) with a copy of saturn baked in at `/usr/local/bin/saturn` so nested saturn works.

```sh
curl -fsSL <url>/saturn -o ~/.local/bin/saturn && chmod +x ~/.local/bin/saturn
saturn base default
```

Custom base image (must keep `COPY saturn /usr/local/bin/saturn`):

```sh
$EDITOR my-base.Dockerfile
saturn base build my-base.Dockerfile
```

## Usage

### Commands

| Command | What it does |
|---|---|
| `saturn new [dir] [flags]` | Seed `<dir>/.saturn/{Dockerfile,compose.yaml}`. Default `dir`=cwd. |
| `saturn base default` | Rebuild `localhost/saturn-base:latest` from the inlined minimal Dockerfile. |
| `saturn base build <file>` | Rebuild the base from a user-supplied Dockerfile. |
| `saturn shell` | Alias for `saturn exec dev bash`. |
| `saturn <anything else>` | Forwarded to `docker compose -f .saturn/compose.json -p <workspace> <args>`. |

Pass-through covers the full docker-compose surface: `up`, `up -d`, `down`, `exec dev <cmd>`, `logs -f`, `ps`, `restart dev`, `build`, and so on.

`saturn new` flags (all independently opt-in):

| Flag | Adds to `Dockerfile` | Adds to `compose.yaml` volumes |
|---|---|---|
| `--ssh` | `apt install openssh-client` | `${HOME}/.ssh:/root/.ssh` |
| `--gh` | `apt install gh` | `${HOME}/.config/gh:/root/.config/gh` |
| `--claude` | `apt install nodejs npm` + `npm i -g @anthropic-ai/claude-code` | `${HOME}/.claude:/root/.claude` + `${HOME}/.claude.json:/root/.claude.json` |
| `--codex` | `apt install nodejs npm` + `npm i -g @openai/codex` | `${HOME}/.codex:/root/.codex` |
| `--socket` | (none) | `${SATURN_SOCK}:/var/run/docker.sock` (lets inner saturn reach the host engine) |

(When both `--claude` and `--codex` are passed, the `nodejs npm` install is deduplicated.)

### Starting a workspace

```sh
saturn base default                              # one-time: build saturn-base

saturn new ~/code/myproj --ssh --claude --socket # seed templates + auto-create ~/.ssh, ~/.claude, ~/.claude.json
$EDITOR ~/code/myproj/.saturn/Dockerfile         # optional: add more tooling
cd ~/code/myproj && saturn up -d                 # build workspace image + start container
```

Equivalent with cwd:

```sh
mkdir -p ~/code/myproj && cd ~/code/myproj
saturn new --ssh --claude --socket
saturn up -d
```

Inside the container, you start in `/root/myproj` — which is `~/code/myproj` on the host, bind-mounted.

### Day-to-day

```sh
cd ~/code/myproj
saturn shell            # interactive bash in the `dev` service
saturn exec dev make test
saturn logs -f dev
saturn down
```

Saturn discovers the workspace by walking cwd upward for `.saturn/compose.yaml`. To see running saturn containers:

```sh
docker ps --filter name=saturn_
```

### Nested saturn

Every saturn container has `saturn` and `docker` installed, plus the host engine socket mounted (if you passed `--socket` to `saturn new`). Inside, you can launch a sibling container on the host engine for any subdirectory of the current workspace:

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

## How it works

### The `.saturn/` layout

```
<workspace>/
  .saturn/
    Dockerfile         # FROM localhost/saturn-base:latest + your tooling
    compose.yaml       # source of truth; you edit this
    compose.json       # regenerated every saturn invocation (translate output)
```

`saturn new` writes the first two. `compose.json` is a derivative — saturn rewrites it each time based on whether you're on host or in a guest container.

### Seeded `compose.yaml` shape

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

### The translation pipeline

Every pass-through command (`saturn up`, `saturn down`, etc.) goes through:

1. `docker compose -f .saturn/compose.yaml -p <workspace> config --format json` — compose parses and normalizes the spec (env substitution, relative paths → absolute, short-form volumes → long-form).
2. **Host mode**: the spec is written verbatim to `.saturn/compose.json`.
3. **Guest mode** (`SATURN_IN_GUEST=1` set inside a saturn container):
   - For each service with a `build:` stanza, saturn runs `docker build` itself (client reads context via inside path; daemon stores image on host engine), then strips `build:` from the service.
   - Saturn asks the host engine for its own container's `.Mounts` list via `docker inspect $(socket.gethostname())` and translates every bind-mount source: find the longest mount whose destination is an ancestor of the source, replace the source with the mount's host path + relative suffix. Unresolvable sources → fail fast.
   - Translated spec → `.saturn/compose.json`.
4. `docker compose -f .saturn/compose.json -p <workspace> <original argv>`.

### The base image

Minimal: Debian trixie + `docker-cli` + `docker-compose` (compose v2 plugin) + `python3` + `git` + `curl` + `saturn` at `/usr/local/bin/saturn` + `ENV IS_SANDBOX=1`. No ssh/gh/nodejs/claude/codex — those move into per-workspace Dockerfiles when you pass `--ssh`, `--gh`, `--claude`, `--codex` to `saturn new`.

## Avoiding podman storage races

Rootless podman has no always-on daemon. Every `podman` CLI invocation opens `~/.local/share/containers/storage/` directly and mutates it. **Concurrent invocations race and can corrupt the store.**

The fix: route every operation through the user-level podman API service, which serializes mutations.

```sh
# one-time
systemctl --user enable --now podman.socket

# ~/.bashrc
export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock
export DOCKER_BUILDKIT=0
```

Always use `docker` instead of `podman`. Saturn and compose both funnel through `DOCKER_HOST` automatically. `DOCKER_BUILDKIT=0` is required because podman's docker-compat socket doesn't serve the BuildKit API.

**Don't mix.** Every direct `podman` invocation bypasses the serializer and reintroduces the race.

## What containers can run saturn

`saturn up` produces containers that satisfy this automatically — this section matters only if you're writing your own image or launcher.

### Image requirements

Any Linux image with:

- `python3` (stdlib-only; no third-party deps)
- `docker` CLI with the `compose` plugin (Debian: `docker-cli` + `docker-compose` packages)
- `/usr/local/bin/saturn` (the script, mode 0755)

The shipped `saturn-base` satisfies all three. Workspace images inherit by `FROM localhost/saturn-base:latest`.

### Runtime bind-mounts

- The host engine socket at `/var/run/docker.sock`. Required for saturn to run `docker compose` + self-inspect.
- The workspace's host dir at `/root/<basename>`. Required so saturn inside can find `.saturn/compose.yaml` (walk cwd upward).
- Anything else the user's `compose.yaml` declares. No more "mixin slot" conventions — just bind mounts in compose.

### Env var contract

When saturn runs inside a rootless container, it re-reads this tiny block at import:

| Var | Value | Why |
|---|---|---|
| `SATURN_IN_GUEST` | `1` | Flips `IS_HOST=False`. Enables reverse mount lookup + guest-mode pre-build-then-strip. Without it, saturn does a naive pass-through with no translation. |
| `SATURN_SOCK` | `/var/run/docker.sock` | Sets `DOCKER_HOST=unix://<SATURN_SOCK>` at import. Re-exported so `${SATURN_SOCK}` substitutes inside the workspace compose.yaml. |

Optional:

| Var | Default | Notes |
|---|---|---|
| `SATURN_BASE_IMAGE` | `localhost/saturn-base:latest` | Override if your workspace Dockerfiles `FROM` a different base. |
| `IS_SANDBOX` | (baked in at `1` in saturn-base) | Tools like Claude Code use this as a marker that root is intentional. |

Reverse lookup needs the container's **hostname** to match what `docker inspect` knows. Saturn uses `socket.gethostname()` (which returns the container's actual hostname, regardless of env export). Docker/compose default the hostname to the short container id. **Do not override `hostname:` in your workspace compose.yaml** — saturn's self-inspect will fail.

### Minimal example (custom launcher)

```sh
# Launch a container so saturn inside can work:
docker run -d --init --name my-container \
  -v /run/user/1000/podman/podman.sock:/var/run/docker.sock \
  -v /path/to/ws:/root/ws \
  -v $HOME/.ssh:/root/.ssh \
  -e SATURN_IN_GUEST=1 \
  -e SATURN_SOCK=/var/run/docker.sock \
  -w /root/ws \
  localhost/saturn-base:latest

# Inside:
docker exec -it my-container bash
cd /root/ws
saturn up -d
```

## Naming

Container name: `saturn_<basename>`. Image name: `localhost/saturn-<basename>:latest`. Compose project name: `<basename>`. Two workspaces with the same basename collide at `up` time (docker's "container name in use"). Rename a directory to disambiguate.

## Security note

Saturn bind-mounts whatever your workspace `compose.yaml` declares. With `saturn new --socket` the list is: host engine socket (full engine control), workspace dir, plus any credential paths from `--ssh/--gh/--claude/--codex`. This is a meaningful blast radius; acceptable for a personal dev tool, not for untrusted code. `IS_SANDBOX=1` is a tool-level affirmation that root is intentional, not an actual sandbox.
