# Workspace (the `.saturn/` directory)

> A workspace is any directory containing `.saturn/compose.yaml`. `saturn new [dir] [--flags]` seeds the template pair (`Dockerfile` + `compose.yaml`); every other subcommand finds the workspace by walking cwd upward.

## Overview

There is no global registry of workspaces. A directory *becomes* a workspace the moment it has a `.saturn/` subdir with a `compose.yaml` in it. `saturn new` is the shortcut for producing that pair from a small set of opt-in flags; it's otherwise an ordinary filesystem operation.

The workspace's **basename** (the dir's final path component) drives container and image identity: `saturn_<basename>`, `localhost/saturn-<basename>:latest`, container cwd `/root/<basename>`. Compose project name is also set to the basename (via `-p <basename>` on every invocation) so compose's default naming (from the compose file's dir = `.saturn`) doesn't collide across workspaces.

## Provided APIs

### `cmd_new(args) -> None`

Seeds `<target>/.saturn/{Dockerfile,compose.yaml}` from templates.

1. Resolve `target` (cwd default). `mkdir -p` if missing.
2. Validate basename (non-empty, no leading `.`, no spaces — it has to be a valid docker image tag component).
3. `mkdir -p <target>/.saturn`; write `Dockerfile` and `compose.yaml` if absent (never overwrites).
4. Host mode only: for each flag, auto-create the host-side source path if missing (`mkdir -p` for dirs, `touch` for files) so the first `up` doesn't fail the bind mount. Guest mode skips auto-create.

Flags are independently opt-in; omitting all gives a minimal workspace with just the source tree bind-mounted. Order is not significant.

| Flag | Dockerfile effect | compose.yaml effect | Auto-create target |
|---|---|---|---|
| `--ssh` | `RUN apt-get install openssh-client` | `- ${HOME}/.ssh:/root/.ssh` | `~/.ssh` (dir) |
| `--gh` | `RUN apt-get install gh` | `- ${HOME}/.config/gh:/root/.config/gh` | `~/.config/gh` (dir) |
| `--claude` | `RUN curl -fsSL https://claude.ai/install.sh \| bash` | `- ${HOME}/.claude:/root/.claude`, `- ${HOME}/.claude.json:/root/.claude.json` | `~/.claude` (dir), `~/.claude.json` (file) |
| `--codex` | `RUN apt-get install nodejs npm && npm i -g @openai/codex` | `- ${HOME}/.codex:/root/.codex` | `~/.codex` (dir) |
| `--socket` | (none) | `- ${SATURN_SOCK}:/var/run/docker.sock` | — |

### `_find_workspace() -> Path`

Walks cwd upward until it finds a directory containing `.saturn/compose.yaml`. Exits with a suggestion to run `saturn new` if nothing is found by the filesystem root. This is the lone mechanism that associates a command with a workspace — there are no positional target args on lifecycle commands (`up`, `down`, `shell`, `exec`, etc.). `cd` to switch workspaces.

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
      # (plus one line per selected flag)
```

- `build.context: .` means the `.saturn/` dir is the build context. Small, fast, and `COPY` works on anything you drop into `.saturn/`.
- `..:/root/<name>` — the workspace root (parent of `.saturn/`) is bind-mounted at `/root/<name>`. Compose resolves `..` relative to the compose file's dir, so this works regardless of cwd.
- `${HOME}` and `${SATURN_SOCK}` are compose env substitutions, done at `compose config` time. On host, they expand to the user's home and the real host socket; in guest mode, they expand to `/root` and `/var/run/docker.sock` (inside paths) — which are then reverse-looked-up to host paths. **One compose.yaml, both modes.**

Users can edit `Dockerfile` and `compose.yaml` freely after seeding. Add services, networks, volumes, extra bind mounts, anything compose supports.

## Consumed APIs

- `IS_HOST` from module-level env — gates host-mode auto-create.
- `BASE_IMAGE` constant — referenced by the seeded `FROM` line.

## Workflows

### Creation

```
saturn new                    # cwd becomes a workspace
saturn new ~/code/foo --ssh --socket --claude
```

Both produce `.saturn/Dockerfile` + `.saturn/compose.yaml`. Auto-create ensures host paths (e.g. `~/.claude.json`) exist before first `up`.

### Nested creation (inside a saturn container)

```
saturn new ./sub --socket
```

Works because the current workspace is bind-mounted; `./sub/.saturn/` gets written through the bind mount and shows up on host. No auto-create runs (guest mode).

### Modifying the seeded templates

- Dockerfile: free-form. Install anything you like.
- compose.yaml: free-form, with two conventions saturn relies on for nested `up`:
  - Do not override `hostname:`. Saturn self-inspects the running container by `socket.gethostname()`, which defaults to the short container id that compose sets.
  - Every bind-mount source should be either a path compose can env-substitute to something valid in both modes (`${HOME}/...`, `${SATURN_SOCK}`), or a host-only path if you never plan to run nested.

## Execution-context constraints

- **Basename must be a valid docker image-ref component** (lowercase, `[a-z0-9_-]`). Capital letters produce `localhost/saturn-MyDir:latest` which docker rejects.
- **No global listing.** For cross-workspace visibility, use `docker ps --filter name=saturn_` (or filter by the `com.docker.compose.project` label compose sets).
- **Basename collisions surface at `up` time.** Two workspaces with the same basename map to the same container/image names; the second `up` fails with a "container name in use" error. Rename a dir to disambiguate.
- **`compose.yaml` is the source of truth.** Saturn never writes to it after `new`. The `compose.json` next to it is a regenerated derivative (translated spec) and can be deleted at any time — the next saturn invocation rewrites it.
