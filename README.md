# saturn

Minimal per-project dev-container CLI for rootless podman and rootless Docker. A saturn container runs as a non-root user (`agent`, uid 10001), has the docker CLI wired up to the host engine's socket, and keeps your source in a named volume so your host filesystem stays clean. Works at any nesting level — running `saturn` inside a saturn container creates siblings on the host engine.

**Zero host state.** Projects are discovered by labels on their volumes; all per-project content (Containerfile, source, `.git/`) lives inside `saturn_ws_<name>` and is committed with the project's own git. User-global state (SSH keys, `gh`/Claude/Codex auth, editor config) is expressed via **mixins** — see *Mixins* below.

## Install

Single file. The `saturn-base` Containerfile is inlined in the script; `saturn base default` assembles a temp build context with the inlined recipe plus a copy of saturn itself (for `COPY saturn /usr/local/bin/saturn`, so nesting works).

```sh
curl -fsSL <url>/saturn -o ~/.local/bin/saturn && chmod +x ~/.local/bin/saturn
saturn base default
```

Custom base image:

```sh
saturn base template > my.Containerfile    # print the inlined default
$EDITOR my.Containerfile                    # tweak it — keep `COPY saturn /usr/local/bin/saturn`
saturn base build my.Containerfile          # rebuild localhost/saturn-base from your file
```

Your override must keep `COPY saturn /usr/local/bin/saturn` — saturn copies itself into the build context alongside your file so nesting continues to work.

## Quick start

```sh
./saturn base default                      # one-time: build saturn-base

# Option A — scaffold a fresh project
saturn project new myproj                  # creates labelled ws volume
saturn project shell myproj                # base-image shell on the volumes
  # inside: saturn runtime init              seed .saturn/Containerfile template
  #         $EDITOR .saturn/Containerfile   add project tooling (FROM saturn-base)
  #         exit
saturn up myproj                           # build project image + start container
saturn shell myproj                        # drop into the project container

# Option B — bring your own repo (clone from inside)
saturn project new myproj
saturn project shell myproj
  # inside: git clone <url> .               (repo must have .saturn/Containerfile)
  #         exit
saturn up myproj

# Option C — import an existing host directory
saturn project new myproj
saturn put myproj ~/path/to/existing-project/. .   # trailing /. -> copy contents
  # if the imported tree lacks .saturn/Containerfile, scaffold one:
  # saturn project shell myproj   →   saturn runtime init   →   exit
saturn up myproj

# day to day
saturn shell myproj                        # bash as agent
saturn exec myproj <cmd> [args...]         # one-off command
saturn down myproj                         # stop+remove container (volumes kept)
saturn project ls                          # list projects
saturn project rm myproj                   # remove container, volumes, image

# move files in/out of the project volume (works whether project is up or down)
saturn put myproj <host-src> [<dst>]       # host -> project (default dst = basename)
saturn get myproj <src> [<host-dst>]       # project -> host (default host-dst = .)
```

Inside the container, `saturn runtime info` shows project + paths; the same `saturn` binary also works nested (creates siblings on the host engine via the propagated socket).

## Mixins

Mixins carry user-global state (SSH keys, `gh` tokens, Claude/Codex auth, editor config) into project containers without bind-mounting host directories. Each mixin bundles:

- an optional install snippet spliced into the base Containerfile (`RUN <cmd>`),
- a user-global named volume (`saturn_mixin_<name>`, labeled `saturn.volume=mixin`),
- a target path inside the container where the volume is mounted (with `volume-subpath=` for file targets like `~/.claude.json`).

Built-in mixins: `ssh`, `gh`, `claude`, `claude-json`, `codex`, `emacs`, `xdg-config`.

The default set, used whenever `--mixins` is omitted, is `ssh,claude,claude-json,codex,gh` — so a bare `saturn up myproj` mounts all of these (and the first-use base-image build installs their tools). Pass `--mixins ''` to opt out, or `--mixins <csv>` to pick a different set.

```sh
# 1. Install the tools into saturn-base (run once, or whenever you change the list):
saturn base default                          # uses defaults: ssh,claude,claude-json,codex,gh
saturn base default --mixins ssh,gh,claude,claude-json   # pick a different set

# 2. Populate the user-global state interactively — shell has ONLY mixin volumes mounted:
saturn project config                        # defaults: ssh,claude,claude-json,codex,gh mounted
saturn project config --mixins ssh           # just ssh — then: ssh-keygen -t ed25519
saturn project config --mixins gh            # just gh — then: gh auth login
saturn project config --mixins claude-json,emacs,xdg-config  # the non-default ones

# 3. Mount the state into a project container:
saturn up myproj                             # defaults: ssh,claude,claude-json,codex,gh
saturn up myproj --mixins ssh,gh,claude,claude-json,emacs    # custom set
saturn up myproj --mixins ''                 # opt out of all mixins
```

Mixin volumes live outside project lifecycle — `saturn project rm` never touches them. To edit the mixin registry (add a tool, change a command or target), edit `MIXINS` in the `saturn` script itself.

`saturn base build <file>` does not accept `--mixins` (user files are used verbatim). Combine a custom base with mixins via: `saturn base template --mixins ... > my.Containerfile`, edit, `saturn base build my.Containerfile`.

Engine requirement: `volume-subpath=` (used for file-target mixins like `claude-json`) needs Docker 25.0+ or Podman 4.7+.

## Avoiding podman storage races

Rootless podman has no always-on daemon. Every `podman` CLI invocation opens `~/.local/share/containers/storage/` directly and mutates it. **Concurrent invocations race and can corrupt the store** — resulting in cryptic `locating item named "manifest"` errors on later calls.

The fix: **route every operation through the user-level podman API service**, which serializes store mutations the same way `dockerd` does.

```sh
# ensure the service is enabled (one-time):
systemctl --user enable --now podman.socket

# and in your shell (~/.bashrc):
export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock
export DOCKER_BUILDKIT=0
```

Then always use `docker` instead of `podman` — the docker CLI speaks podman's docker-compat API, and all invocations funnel through the single service process. `DOCKER_BUILDKIT=0` is required because podman's socket doesn't serve the BuildKit API; the classic builder talks the protocol it does serve.

Saturn already does this internally. If you sometimes reach for `podman` directly out of habit, know that every such invocation bypasses the serializer and reintroduces the race. **Don't mix the two.**

## What containers can run saturn

Any Linux image that contains:

- `python3` (stdlib is enough; no third-party deps)
- the `docker` CLI (`docker-cli` package on Debian, `docker-cli` on Alpine)
- `sudo` with NOPASSWD configured for the non-root user
- a non-root user to run as (convention: `agent`, uid/gid 10001)
- `/usr/local/bin/saturn` (the script, mode 0755)

At runtime the container additionally needs:

- the host engine socket bind-mounted at `/var/run/docker.sock`
- env vars `SATURN_SOCK=/var/run/docker.sock`, `SATURN_HOST_SOCK=<real host path>`, `SATURN_SUDO=1`, and `SATURN_PROJECT=<name>` — **auto-propagated by `saturn up` and `saturn project shell`** when one saturn container spawns another

The shipped `saturn-base` image (Debian trixie slim) satisfies all of these. Project images inherit everything by starting from it:

```dockerfile
# .saturn/Containerfile  (lives inside saturn_ws_<name>, committed with your repo)
FROM localhost/saturn-base:latest

USER 0
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ripgrep \
 && rm -rf /var/lib/apt/lists/*
USER agent:agent
```

Roll-your-own equivalents on Alpine/Fedora/etc. work too — the list above is what matters, not the distro.

## Security note

Bind-mounting the host engine socket into a container is equivalent to granting full control of your rootless engine ("host-you"). Saturn is a dev tool; do not expose the socket into production containers.
