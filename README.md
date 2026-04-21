# saturn

Minimal dev-container CLI for rootless podman and rootless Docker. A **workspace** is any directory you put a `.saturn/` marker in. `saturn up [dir]` launches a container that bind-mounts that directory at `/root/<basename>`, along with a set of mixin bind-mounts for user-global state (SSH keys, tokens, etc.) and the engine socket. The container runs as root — rootless userns maps that back to your host user, so files written from inside land on disk with host ownership. Works at any nesting level.

## Install

Single file. The `saturn-base` Containerfile is inlined in the script; `saturn base default` assembles a temp build context with the inlined recipe plus a copy of saturn itself (for `COPY saturn /usr/local/bin/saturn`, so nesting works).

```sh
curl -fsSL <url>/saturn -o ~/.local/bin/saturn && chmod +x ~/.local/bin/saturn
saturn base default
```

Custom base image:

```sh
saturn base template > my.Containerfile    # print the inlined default
$EDITOR my.Containerfile                    # tweak — keep `COPY saturn /usr/local/bin/saturn` and `ENV IS_SANDBOX=1`
saturn base build my.Containerfile          # rebuild localhost/saturn-base from your file
```

## Usage

### Commands

```
saturn new [dir]         make <dir> a workspace (mkdir -p + seed .saturn/Containerfile)
saturn up  [dir]         build workspace image + start saturn_<basename> container
saturn down              stop+remove the container for cwd's workspace
saturn shell             interactive bash in cwd's container (cwd inside = /root/<basename>)
saturn exec <cmd>...     run a command in cwd's container (argv passes through verbatim)

saturn up  [dir] --mixins <csv>         pick a different mixin set (default: ssh,claude,codex,gh)
saturn up  [dir] --mixin-root <dir>     re-root mixin default paths under <dir> instead of $HOME

saturn base default      (re)build saturn-base with the current default mixins
saturn base template     print the inlined Containerfile to stdout
saturn base build <file> (re)build saturn-base from your own Containerfile (verbatim)
```

`new` and `up` take an optional target dir (default: cwd). `down`, `shell`, `exec` always act on cwd — `cd` to switch context.

### Starting a workspace

```sh
saturn base default                       # one-time: build saturn-base

saturn new ~/code/myproj                  # mkdir -p + seed .saturn/Containerfile
$EDITOR ~/code/myproj/.saturn/Containerfile   # optional: add workspace tooling
saturn up ~/code/myproj                   # build workspace image + start container
```

Equivalent with cwd:

```sh
mkdir -p ~/code/myproj && cd ~/code/myproj
saturn new && saturn up
```

Inside the container, you start in `/root/myproj` — which is `~/code/myproj` on the host, bind-mounted at a different path. Writes there land on disk with host-user ownership (rootless userns).

### Day-to-day

```sh
cd ~/code/myproj
saturn shell            # interactive shell
saturn exec make test   # one-off command; user flags pass through (`exec` intercepts argv)
saturn down             # stop+remove container
```

To see running saturn containers across workspaces:

```sh
docker ps --filter label=saturn.workspace
```

The label holds the host path of each container's workspace.

### Nested saturn

`saturn` is installed in every container. Inside a workspace container, you can launch a sibling on the host engine for any subdirectory of the current workspace:

```sh
# inside ~/code/myproj's container (cwd = /root/myproj):
mkdir sub && saturn new sub && saturn up sub
# ^ saturn_sub runs on the host engine; its workspace host path is ~/code/myproj/sub.
```

`up` inside a container rejects targets outside the current workspace — saturn needs the relative path to derive the host-side source.

## How the container is wired

Every `saturn up` does:

- `-v <target_host_path>:/root/<basename>` — the workspace is bind-mounted at `/root/<basename>` (no path symmetry). Cwd is set there.
- For each selected mixin slot, `-v <host_path>:<target>` where `<target>` is a fixed container-side path (e.g. `/root/.ssh`) and `<host_path>` is either `$SATURN_MIXIN_<SLOT>` (if set) or a schema default under `$HOME` (auto-created on the host if missing). See **Mixins** below.
- `-v $SOCK:/var/run/docker.sock` — the engine socket, so `docker` inside works against the host engine.
- `-e SATURN_IN_GUEST=1` — tells the inner saturn it's inside a container.
- `-e SATURN_HOST_HOME=... -e SATURN_HOST_SOCK=... -e SATURN_HOST_WORKSPACE=<host_path> -e SATURN_WORKSPACE=/root/<basename> -e SATURN_MIXIN_<SLOT>=<host_path>` — propagated for nesting; inner saturn uses these to re-bind-mount from real host paths.
- `--label saturn.workspace=<host_path>` — informational, for `docker ps --filter`.
- Runs as root; rootless userns maps that to your host user, so writes land on disk with host-user ownership.

Container `HOME` stays at its image default (`/root`). The workspace lives at `/root/<basename>` and mixin slots at `/root/.ssh`, `/root/.claude`, `/root/.claude.json`, `/root/.codex`, `/root/.config/gh`, so tools looking up `~/...` find their bind-mounted state naturally.

The base image carries `ENV IS_SANDBOX=1`, which tells Claude Code (and similar tools) that `--dangerously-skip-permissions` is intentional. Without it they refuse to run as root.

## Mixins

Mixins are named bundles of (slot records + install snippet) that carry user-global state into workspace containers without exposing the whole `$HOME`. Each mixin declares:

- **`slots`** — one or more per-path records. Each slot has an `env` (the host-path env var, e.g. `SATURN_MIXIN_SSH`), a fixed container `target` (e.g. `/root/.ssh`), a `kind` (`file` or `dir`), and a `default_host` fallback (HOME-relative) used in host mode when `env` is unset.
- **`setup`** — an optional shell snippet spliced into the base Containerfile as `RUN <setup>`, so the tool is installed once at base-image build time.

Built-ins: `ssh`, `gh`, `claude`, `codex`. Edit the `MIXINS` dict at the top of the `saturn` script to add your own.

Defaults: `--mixins` omitted → `ssh,claude,codex,gh`. Pass `--mixins ''` to opt out. Pass `--mixins <csv>` to pick a different set.

```sh
# Install the tools into saturn-base:
saturn base default                          # uses defaults
saturn base default --mixins ssh,gh          # different set
saturn base default --mixins ''              # no mixin tools

# Zero-config up (host mode): missing slot host paths are auto-created
# (dirs via mkdir -p, files via touch) so `saturn up` never fails on
# first-time state.
saturn up ~/code/myproj                        # defaults
saturn up ~/code/myproj --mixins ssh,gh        # custom set
saturn up ~/code/myproj --mixins ''            # plain container, no mixin mounts

# Set up credentials on the host (the auto-created paths are empty — tools
# populate them):
ssh-keygen -t ed25519                        # ~/.ssh
gh auth login                                # ~/.config/gh
# claude / codex: run once on host to populate ~/.claude + ~/.claude.json / ~/.codex

# Explicit isolation — override any slot's host path with its env var:
SATURN_MIXIN_CLAUDE_JSON=~/code/myproj/.sandbox/claude.json saturn up ~/code/myproj
# ^ the container's ~/.claude.json is now backed by the workspace-local file,
#   not the host user's real ~/.claude.json.

# Or re-root every mixin default under a single parent dir with --mixin-root
# (host mode only; per-slot env vars still override):
saturn up ~/code/myproj --mixin-root ~/code/myproj/.sandbox-home
# ^ uses .sandbox-home/.ssh, .sandbox-home/.claude, .sandbox-home/.claude.json, etc.
```

Inside a saturn container (`SATURN_IN_GUEST=1`), every slot env var for selected mixins must be set — the outer `saturn up` propagates them. Missing → fail with a labelled list pointing at the outer invocation.

## Workspace Containerfiles

`saturn new [dir]` seeds `<dir>/.saturn/Containerfile` with:

```dockerfile
FROM localhost/saturn-base:latest

# Add workspace tooling here. Example:
#   RUN apt-get update \
#    && apt-get install -y --no-install-recommends ripgrep \
#    && rm -rf /var/lib/apt/lists/*
```

Edit freely. Everything installs and runs as root. If the file is missing, `saturn up` just runs the base image directly.

## Avoiding podman storage races

Rootless podman has no always-on daemon. Every `podman` CLI invocation opens `~/.local/share/containers/storage/` directly and mutates it. **Concurrent invocations race and can corrupt the store** — cryptic `locating item named "manifest"` errors follow.

The fix: **route every operation through the user-level podman API service**, which serializes store mutations like `dockerd` does.

```sh
# one-time
systemctl --user enable --now podman.socket

# ~/.bashrc
export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock
export DOCKER_BUILDKIT=0
```

Then always use `docker` instead of `podman` — the docker CLI speaks podman's docker-compat API, and all invocations funnel through the single service process. `DOCKER_BUILDKIT=0` is required because podman's socket doesn't serve the BuildKit API.

Saturn already does this internally. If you sometimes reach for `podman` directly, know that every such invocation bypasses the serializer and reintroduces the race. **Don't mix the two.**

## What containers can run saturn

`saturn up` produces containers that satisfy this automatically — this section matters only if you're writing your own image or launcher and want `saturn` to work from inside.

### Image requirements

Any Linux image that contains:

- `python3` (stdlib is enough; no third-party deps)
- the `docker` CLI (`docker-cli` on Debian)
- `/usr/local/bin/saturn` (the script, mode 0755)

The shipped `saturn-base` image (Debian trixie slim) satisfies all three. Workspace images inherit them by starting from it:

```dockerfile
# <workspace>/.saturn/Containerfile
FROM localhost/saturn-base:latest
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ripgrep \
 && rm -rf /var/lib/apt/lists/*
```

### Runtime bind-mounts

- Host engine socket at `/var/run/docker.sock` (so the in-container `docker` CLI reaches the host engine).
- The workspace's host dir at `/root/<basename>` (so `saturn` inside can read its own `.saturn/Containerfile` and the user's files).
- For each mixin slot the workspace will use, the host-side path at the slot's fixed container target (e.g. `/root/.ssh`, `/root/.claude.json`). Mixins that aren't mounted can't be selected from inside.

### Env var contract

When saturn starts up inside a rootless container, it re-reads this block on `import` and derives `IS_HOST`, the DOCKER_HOST, and the workspace/mixin resolution. Outer `saturn up` sets all of these for its children; if you're writing your own launcher you need to supply them yourself.

**Required** (saturn exits fail-fast without these):

| Var | Value | Why |
|---|---|---|
| `SATURN_IN_GUEST` | `1` | Flips `IS_HOST=False` — disables host-mode defaults / auto-create. |
| `SATURN_SOCK` | `/var/run/docker.sock` | Sets `DOCKER_HOST=unix://...` at module import. Required because the default derivation (`$XDG_RUNTIME_DIR/podman/podman.sock`) points at a host path that doesn't exist inside the container. |
| `SATURN_HOST_SOCK` | absolute host path of the engine socket | Bind-mount source when this container launches a sibling. Without it, nested `saturn up` would try to pass a container path to the daemon. |
| `SATURN_HOST_WORKSPACE` | absolute host path of this container's workspace | Consumed by `_resolve_target` inside: `target_host_path = SATURN_HOST_WORKSPACE / target.relative_to(SATURN_WORKSPACE)`. |
| `SATURN_WORKSPACE` | container-side path (i.e. `/root/<basename>`) | Second half of the translation pair — the "current workspace" inside the container. Target must be under it. |
| `SATURN_MIXIN_<SLOT>` | absolute host path for each slot of every mixin the workspace uses | Bind-mount source for that slot when a sibling is launched. **One var per slot** (e.g. `SATURN_MIXIN_SSH`, `SATURN_MIXIN_CLAUDE`, `SATURN_MIXIN_CLAUDE_JSON`). Missing → nested `saturn up --mixins <that-mixin>` exits fail-fast. |

**Optional** (saturn has sensible defaults inside):

| Var | Default | Notes |
|---|---|---|
| `SATURN_ENGINE` | `podman` | Identifies the host engine family. Affects `DOCKER_BUILDKIT` (set to `0` for podman, unset for docker). |
| `SATURN_HOST_HOME` | `Path.home()` inside | Informational inside; used as the base for mixin defaults (moot in guest mode since defaults don't apply). |
| `SATURN_BASE_IMAGE` | `localhost/saturn-base:latest` | Override if your sibling launches should `FROM` a different base. |

**Base image convenience:**

- `IS_SANDBOX=1` baked into the base image tells Claude Code (and similar tools) that `--dangerously-skip-permissions` is intentional. Without it they refuse to run as root.

### Minimal example (custom launcher)

```sh
# Launch a container so that `saturn` inside can work:
docker run -d --init --name my-container \
  -v /run/user/1000/podman/podman.sock:/var/run/docker.sock \
  -v /path/to/ws:/root/ws \
  -v $HOME/.ssh:/root/.ssh \
  -e SATURN_IN_GUEST=1 \
  -e SATURN_SOCK=/var/run/docker.sock \
  -e SATURN_HOST_SOCK=/run/user/1000/podman/podman.sock \
  -e SATURN_HOST_WORKSPACE=/path/to/ws \
  -e SATURN_WORKSPACE=/root/ws \
  -e SATURN_MIXIN_SSH=$HOME/.ssh \
  -w /root/ws \
  localhost/saturn-base:latest

# Now inside `my-container`, `saturn up sub` / `saturn exec <...>` work as expected.
```

## Naming

The container name is `saturn_<basename>`; the image name is `localhost/saturn-<basename>:latest`. Two workspaces with the same basename (e.g. `~/code/foo` and `~/tmp/foo`) can't run concurrently — the second `saturn up` surfaces a "container name in use" error. Rename a workspace directory to disambiguate.

## Security note

Saturn bind-mounts the host engine socket (full control of your rootless engine), the workspace's host dir at `/root/<basename>`, and each selected mixin slot's host path (credentials like SSH keys and API tokens, by default under `$HOME`). The blast radius is smaller than mounting the whole `$HOME`, and per-slot env vars + `--mixin-root` let you point the mixin set at isolated scratch locations. Still not small — the socket alone grants full engine access. Dev-time convenience only; do not run untrusted code through saturn. `IS_SANDBOX=1` is a tool-level affirmation that root is intentional, not an actual sandbox.
