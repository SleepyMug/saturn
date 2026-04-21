# saturn

Minimal per-project dev-container CLI for rootless podman and rootless Docker. Each project is a host directory under `$HOME/saturn/<name>/`; the engine socket and host `$HOME` are bind-mounted in; the container runs as root (rootless userns maps that back to your host user). Works at any nesting level — `saturn` inside a saturn container creates siblings on the host engine.

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

## Quick start

```sh
saturn base default                 # one-time: build saturn-base
saturn new myproj                   # creates ~/saturn/myproj/ with a template .saturn/Containerfile
$EDITOR ~/saturn/myproj/.saturn/Containerfile   # optional: add project tooling
saturn up myproj                    # build project image + start container
saturn shell myproj                 # interactive shell; cwd = ~/saturn/myproj

# import an existing repo:
saturn new myproj                   # creates the dir
git clone <url> ~/saturn/myproj     # (or edit files directly)
saturn up myproj

# day to day:
saturn ls                           # list projects
saturn exec myproj <cmd> [args...]  # one-off command
saturn down myproj                  # stop+remove container (host dir kept)
saturn rm myproj                    # stop container, remove image, rm -rf host dir (confirms)
saturn rm myproj -f                 # skip confirmation
```

Inside the container, `cd` into `~/saturn/myproj` — the same path as on the host (`$HOME` is bind-mounted path-symmetrically). The `saturn` CLI works from inside too; it creates siblings on the host engine via the propagated socket.

## How the container is wired

Every `saturn up` does:

- `-v $HOME/saturn:$HOME/saturn` — the projects root is visible inside at the same path, so you can navigate/create any project from inside.
- For each selected mixin path, `-v $HOME/<path>:$HOME/<path>` — specific credential/config paths brought in path-symmetrically (see **Mixins** below). No other parts of `$HOME` are exposed.
- `-v $SOCK:/var/run/docker.sock` — the engine socket, so `docker` inside works against the host engine.
- `-e HOME=$HOME` — container-root's home is set to your host home so `~/.ssh`, `~/.claude.json`, etc. resolve to the bind-mounted host paths (when their mixin is selected).
- `-e SATURN_HOST_HOME=$HOME -e SATURN_HOST_SOCK=$SOCK` — propagated for nesting; saturn-in-saturn uses these to bind-mount the real host paths into its siblings.
- `--label saturn.project=<name>` — for `saturn ls`.
- Runs as root; rootless userns maps that to your host user, so writes land on disk with host-user ownership.

The base image carries `ENV IS_SANDBOX=1`, which tells Claude Code (and similar tools) that `--dangerously-skip-permissions` is intentional. Without it they refuse to run as root.

## Mixins

Mixins are named bundles of (HOME-relative paths + install snippet) that carry user-global state into project containers without exposing the whole `$HOME`. Each mixin declares:

- **`paths`** — a list of HOME-relative strings (e.g. `.ssh`, `.claude.json`). Each is bind-mounted path-symmetrically (`-v $HOME/<path>:$HOME/<path>`) on `saturn up`.
- **`setup`** — an optional shell snippet spliced into the base Containerfile as `RUN <setup>`, so the tool is installed once at base-image build time.

Built-ins: `ssh`, `gh`, `claude`, `codex`. Edit the `MIXINS` dict at the top of the `saturn` script to add your own.

Defaults: `--mixins` omitted → `ssh,claude,codex,gh`. Pass `--mixins ''` to opt out. Pass `--mixins <csv>` to pick a different set.

```sh
# Install the tools into saturn-base:
saturn base default                          # uses defaults
saturn base default --mixins ssh,gh          # different set
saturn base default --mixins ''              # no mixin tools

# Set up credentials on the host (once — they live in your real $HOME):
ssh-keygen -t ed25519                        # ~/.ssh
gh auth login                                # ~/.config/gh
# claude / codex: run once on host to populate ~/.claude + ~/.claude.json / ~/.codex

# Mount them into project containers:
saturn up myproj                             # defaults
saturn up myproj --mixins ssh,gh             # custom set
saturn up myproj --mixins ''                 # plain container
```

If a selected mixin's path doesn't exist on the host, `saturn up` fails fast with the missing path named. Create it (or drop the mixin) and retry.

## Project Containerfiles

`saturn new <name>` seeds `~/saturn/<name>/.saturn/Containerfile` with:

```dockerfile
FROM localhost/saturn-base:latest

# Add project tooling here. Example:
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

Any Linux image that contains:

- `python3` (stdlib is enough; no third-party deps)
- the `docker` CLI (`docker-cli` on Debian)
- `/usr/local/bin/saturn` (the script, mode 0755)

At runtime the container additionally needs:

- the host engine socket bind-mounted at `/var/run/docker.sock`
- the projects root (`$HOME/saturn/`) bind-mounted path-symmetrically
- any selected mixin paths bind-mounted path-symmetrically
- env vars `SATURN_SOCK=/var/run/docker.sock`, `SATURN_HOST_SOCK=<real host path>`, `SATURN_HOST_HOME=<real host path>`, `HOME=<real host path>` — **auto-propagated by `saturn up`** when one saturn container spawns another
- `IS_SANDBOX=1` if you want tools like Claude Code to accept running as root

The shipped `saturn-base` image (Debian trixie slim) satisfies all of these. Project images inherit everything by starting from it:

```dockerfile
# ~/saturn/<name>/.saturn/Containerfile
FROM localhost/saturn-base:latest
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ripgrep \
 && rm -rf /var/lib/apt/lists/*
```

## Security note

Saturn bind-mounts the host engine socket (full control of your rootless engine), the projects root (`$HOME/saturn/`, so inside saturn can manage siblings), and each selected mixin path (credentials like SSH keys and API tokens). The blast radius is smaller than mounting the whole `$HOME`, but still not small — the socket alone grants full engine access. Dev-time convenience only; do not run untrusted code through saturn. `IS_SANDBOX=1` is a tool-level affirmation that root is intentional, not an actual sandbox.
