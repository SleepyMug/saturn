# Base image

> Single-file distribution: the saturn-base Containerfile is inlined as a Python string (HEAD + TAIL, with mixin setup `RUN` lines spliced between); the build context is assembled in a temp dir with a copy of saturn itself.

## Overview

The saturn-base image (`localhost/saturn-base:latest` by default) is the parent image for every project image. Its contract: Debian-trixie-slim base; `docker-cli`, `ca-certificates`, `python3`, and `git` installed; plus whatever tools the selected mixins' setup scripts install; `/usr/local/bin/saturn` present and executable; `IS_SANDBOX=1` in the config env; `CMD ["sleep", "infinity"]`. No user creation, no sudo, no `USER` directive — containers run as root.

The build is self-contained. Builds write the rendered Containerfile into a `TemporaryDirectory`, `shutil.copy` the running saturn script alongside it, then invoke `docker build` against that temp context. Distribution is one file: `curl .../saturn -o ~/.local/bin/saturn && chmod +x`.

The `base` command group exposes three operations: `template` (print the rendered Containerfile), `default` (rebuild from it), and `build <file>` (rebuild from a user-supplied Containerfile). `template` and `default` accept `--mixins`; `build` does not (user-supplied files are used verbatim).

## Provided APIs

### `BASE_IMAGE: str`

`os.environ.get("SATURN_BASE_IMAGE", "localhost/saturn-base:latest")`.

### `BASE_CONTAINERFILE_HEAD` / `BASE_CONTAINERFILE_TAIL: str`

Split halves of the inlined default. HEAD is `FROM debian:trixie-slim` + base packages (`docker-cli ca-certificates python3 git`). TAIL is `COPY saturn` + `chmod` + `ENV IS_SANDBOX=1` + `CMD`. Mixin setups splice between them as `RUN <setup>` lines (via [`_render_base_containerfile`](../mixins/index.md#provided-apis)).

### `_build_base(containerfile_text: str) -> None`

Used by `ensure_base()`, `cmd_base_default`, and `cmd_base_build`.

1. Open a `tempfile.TemporaryDirectory(prefix="saturn-base-")` (auto-cleanup on exit).
2. Write `containerfile_text` into `<tmp>/Containerfile`.
3. `shutil.copy(SCRIPT, <tmp>/saturn)` — always. Custom Containerfiles must keep `COPY saturn /usr/local/bin/saturn` or nesting breaks.
4. `docker build -f <tmp>/Containerfile -t BASE_IMAGE <tmp>`.

`SCRIPT = Path(__file__).resolve()` at module top — so the copy works from any CWD.

### `ensure_base() -> None`

`docker image inspect BASE_IMAGE`; if present, no-op. Else builds with `_render_base_containerfile(DEFAULT_MIXINS)` so a bare `saturn up <name>` produces a base image with the default mixins' tools installed (consistent with the default mount set on `up`).

### `cmd_base_template(args) -> None`

Write the rendered Containerfile (optionally with `--mixins`) to stdout.

### `cmd_base_default(args) -> None`

Force-rebuild. `docker rmi BASE_IMAGE` (ignored if absent), then `_build_base(_render_base_containerfile(mixins))`.

### `cmd_base_build(args) -> None`

Force-rebuild from a user-supplied Containerfile. Errors if the file is missing. **No `--mixins`** — user file is verbatim.

## Consumed APIs

- [`engine`, `engine_ok`](../engine/index.md#provided-apis).
- [`_render_base_containerfile`, `_cli_mixins`, `DEFAULT_MIXINS`](../mixins/index.md#provided-apis).
- `SCRIPT` constant — absolute path to the running saturn script.

## Workflows

### Fresh host, first `saturn up demo`

1. Project directory `$HOST_HOME/saturn/demo` exists (user ran `saturn new demo`).
2. `ensure_base()` — `docker image inspect` fails (image doesn't exist).
3. `_build_base(_render_base_containerfile(DEFAULT_MIXINS))`:
   a. `TemporaryDirectory("saturn-base-")` → `/tmp/saturn-base-xxxxx/`.
   b. Render the HEAD + TAIL with mixin `RUN` lines, write to `Containerfile`.
   c. `shutil.copy("/home/user/.local/bin/saturn", "/tmp/.../saturn")`.
   d. `docker build -f /tmp/.../Containerfile -t localhost/saturn-base:latest /tmp/.../`.
4. `TemporaryDirectory` auto-removes.
5. Proceed with `up` (project image build + container start).

### Custom base

1. `saturn base template [--mixins ...] > ~/my-base/Containerfile`.
2. Edit. Keep `COPY saturn /usr/local/bin/saturn` and `ENV IS_SANDBOX=1`.
3. `saturn base build ~/my-base/Containerfile`.

## Execution-context constraints

- **Classic Docker builder on podman**. `DOCKER_BUILDKIT=0` is forced only when `ENGINE == "podman"` because podman's docker-compat socket doesn't serve BuildKit.
- **COPY ordering**. `COPY saturn` must come after the `apt-get install` layer so `chmod 0755` has `/usr/local/bin/` available.
- **Setup scripts run at build time, not mount time**. They install tools into the base image; they cannot touch the mixin paths that will later be bind-mounted (those are host-side and must already exist on host).
