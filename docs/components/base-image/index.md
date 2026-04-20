# Base image

> Single-file distribution: the saturn-base Containerfile is inlined as a Python string; the build context is assembled in a temp dir with a copy of saturn itself.

## Overview

The saturn-base image (`localhost/saturn-base:latest` by default) is the parent image for every project image. Its contract: Debian-trixie-slim base, `agent` user (uid 10001), docker-cli + sudo + python3 installed, `/usr/local/bin/saturn` present and executable, sudo-NOPASSWD for agent, USER agent, WORKDIR `/home/agent`, CMD `sleep infinity`.

The build is self-contained — saturn's on-disk companions (standalone Containerfile, app.py, etc.) are gone. Builds write a Containerfile (inlined default, or a user-supplied one) into a `TemporaryDirectory`, `shutil.copy` the running saturn script alongside it, then invoke `docker build` against that temp context. Distribution is one file: `curl .../saturn -o ~/.local/bin/saturn && chmod +x`.

The inlined Containerfile is split into `BASE_CONTAINERFILE_HEAD` (FROM + base packages + sudoers) and `BASE_CONTAINERFILE_TAIL` (`COPY saturn` + WORKDIR/USER/CMD). `_render_base_containerfile(mixins)` ([mixins](../mixins/index.md#provided-apis)) splices optional mixin `RUN` lines between them, framed by `USER 0` / `USER agent:agent` so installs run as root.

The `base` command group exposes three operations: `template` (print the rendered Containerfile), `default` (rebuild from it), and `build <file>` (rebuild from a user-supplied Containerfile). `template` and `default` accept `--mixins <list>`; `build` does **not** — user-supplied files are used verbatim. To combine a custom base with mixins, pipe `base template --mixins ...` into a file, edit, then `base build`.

## Provided APIs

### `BASE_IMAGE: str`

`os.environ.get("SATURN_BASE_IMAGE", "localhost/saturn-base:latest")`. Used everywhere an engine call needs the base-image tag.

### `BASE_CONTAINERFILE_HEAD` / `BASE_CONTAINERFILE_TAIL: str`

Split halves of the inlined default. Combined (by `_render_base_containerfile([])`) they form:

- `FROM docker.io/library/debian:trixie-slim`
- `groupadd/useradd` for `agent` uid/gid 10001 with `/bin/bash` shell
- `apt-get install docker-cli sudo ca-certificates python3`
- `/etc/sudoers.d/agent` with `NOPASSWD: ALL`
- (mixin `RUN` lines spliced here — see [mixins](../mixins/index.md#provided-apis))
- `COPY saturn /usr/local/bin/saturn` + `RUN chmod 0755` (portable across docker & podman classic builder; BuildKit `--chmod=` isn't available here)
- `WORKDIR /home/agent`, `USER agent:agent`, `CMD ["sleep", "infinity"]`

`BASE_CONTAINERFILE_TEXT = _render_base_containerfile(DEFAULT_MIXINS)` — the first-use auto-build path uses this, so a bare `saturn up <name>` produces a base image with the default mixins' tools installed. That matches the default mount set on `up`, so the tools are present when the volumes arrive. Opt out via `saturn base default --mixins ''` (explicit empty string) or pick a different set via `base default --mixins ...`.

### `_build_base(containerfile_text: str) -> None`

Used by `ensure_base()`, `cmd_base_default`, and `cmd_base_build`.

1. Open a `tempfile.TemporaryDirectory(prefix="saturn-base-")` (auto-cleanup on exit).
2. Write `containerfile_text` into `<tmp>/Containerfile`.
3. `shutil.copy(SCRIPT, <tmp>/saturn)` — always. Custom Containerfiles must keep `COPY saturn /usr/local/bin/saturn` or nesting breaks.
4. `docker build -f <tmp>/Containerfile -t BASE_IMAGE <tmp>`.

`SCRIPT = Path(__file__).resolve()` at module top — so the copy works from any CWD.

### `ensure_base() -> None`

`docker image inspect BASE_IMAGE`; if present, no-op. Else prints "building base image (first time only)" and calls `_build_base(BASE_CONTAINERFILE_TEXT)`. Used as a prerequisite in almost every top-level command — auto-build always uses the inlined default.

### `cmd_base_template(args) -> None`

Write the rendered Containerfile to stdout. Accepts optional `--mixins <csv>`; without it, renders the plain default. Pipe to a file to seed a custom Containerfile.

### `cmd_base_default(args) -> None`

Explicit rebuild. Accepts optional `--mixins <csv>`. `docker rmi BASE_IMAGE` (ignored if absent), then `_build_base(_render_base_containerfile(mixins))`.

### `cmd_base_build(args) -> None`

Explicit rebuild from a user-supplied Containerfile. Errors if `args.file` is not a regular file; otherwise `docker rmi BASE_IMAGE` (ignored if absent), then `_build_base(path.read_text())`. **Does not accept `--mixins`** — user files are used verbatim.

## Consumed APIs

- [`engine`, `engine_ok`](../engine/index.md#provided-apis).
- [`_render_base_containerfile`, `_parse_mixin_list`](../mixins/index.md#provided-apis) — mixin-aware Containerfile composition for `base template` and `base default`.
- `SCRIPT` constant — absolute path to the running saturn script.

## Workflows

### Fresh host, first `saturn up demo`

1. `project_exists("demo")` → true (user ran `project new` earlier).
2. `ensure_base()` — `docker image inspect` fails (image doesn't exist).
3. `_build_base(BASE_CONTAINERFILE_TEXT)` (equivalent to `_render_base_containerfile([])`):
   a. `TemporaryDirectory("saturn-base-")` → `/tmp/saturn-base-xxxxx/`.
   b. Write the rendered text to `Containerfile`.
   c. `shutil.copy("/home/user/.local/bin/saturn", "/tmp/.../saturn")`.
   d. `docker build -f /tmp/.../Containerfile -t localhost/saturn-base:latest /tmp/.../`.
4. `TemporaryDirectory` auto-removes on context exit.
5. Proceed with the rest of `up` (project image build + container start).

### Custom base for a bespoke project

1. `saturn base template > ~/my-base/Containerfile` — seed with the inlined default.
2. Edit `~/my-base/Containerfile` to taste (e.g. swap `FROM` to `ubuntu:24.04`, add packages); keep `COPY saturn /usr/local/bin/saturn`.
3. `saturn base build ~/my-base/Containerfile` — `cmd_base_build` force-rebuilds `localhost/saturn-base:latest` from the file. Saturn still copies itself into the context alongside.

Subsequent `saturn up <name>` calls skip the rebuild (`ensure_base` sees the image exists). To return to the default: `saturn base default`.

## Execution-context constraints

- **Classic Docker builder only**. `DOCKER_BUILDKIT=0` is forced at module top because podman's docker-compat socket doesn't serve BuildKit.
- **COPY ordering**. The `COPY saturn` step must come after the `apt-get install` layer and before `USER agent` so the subsequent `chmod 0755` runs as root.
- **shutil.copy preserves mode**. On Linux `copy()` also copies mode bits; if the source saturn is `0755`, the dest is `0755`. The explicit `RUN chmod 0755` in the Containerfile is belt-and-suspenders for cases where the source mode is off.
