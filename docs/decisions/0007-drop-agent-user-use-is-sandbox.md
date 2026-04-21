# 0007 — Drop the `agent` user + sudo dance; run as root with `IS_SANDBOX=1`; host bind-mounts replace named volumes

> Supersedes [0002](0002-volume-first-zero-host-state.md), [0003](0003-sudo-over-group-add.md), [0005](0005-lifecycle-vs-content.md), [0006](0006-user-state-mixins.md). 0004 (label-based discovery) still applies, but now the labels live on containers, not volumes.
>
> **Partially revised by [0008](0008-mixins-as-targeted-bind-mounts.md).** The whole-`$HOME` bind-mount proposed here was replaced by selective mixin paths + an always-mounted projects root. The other decisions below (run as root, `IS_SANDBOX=1`, host directories instead of named volumes, `SATURN_HOST_HOME`, path symmetry) stand.

## Context

Two assumptions behind the original design were tested empirically and didn't hold:

1. **"Running as root in the container will break host-file ownership."** Under rootless podman/Docker, the user namespace maps container-uid 0 back to the invoking host user. Files written from inside through a bind-mount land on disk owned by the host user, not by some subuid. No chown gymnastics are needed. The `agent` user (uid 10001), the NOPASSWD sudoers entry, and the `--user 0` / chown-to-10001 calls on volume creation all existed to work around a problem that doesn't exist on the target platform.

2. **"Claude Code refuses `--dangerously-skip-permissions` as root."** True — but setting `IS_SANDBOX=1` in the container env suppresses that check. So the second reason we needed a non-root user also evaporates.

With both pressures gone, the whole edifice can collapse: no user creation, no sudo, no chown, no per-mixin ownership dance, no `USER 0` transitions.

The second simplification follows: if the host filesystem is fine as the source of truth (rootless bind mounts work), then named volumes for ws content and for user-global mixin state are unnecessary complexity. A project directory under `$HOME/saturn/<name>/` replaces `saturn_ws_<name>`; bind-mounting `$HOME` path-symmetrically replaces every mixin volume. `put`/`get` go away because the host dir *is* the content.

## Decision

- **Container runs as root.** Base image is Debian trixie-slim + `docker-cli`, `ca-certificates`, `python3`, `git`; no `groupadd`/`useradd`, no `sudo`, no `USER` directive.
- **`ENV IS_SANDBOX=1`** baked into the base image so Claude Code and similar tools accept `--dangerously-skip-permissions` without a further env tweak.
- **Bind mounts replace named volumes.** Every `docker run` gets `-v $HOST_HOME:$HOST_HOME` (path-symmetric) and `-v $HOST_SOCK:/var/run/docker.sock`. Projects live at `$HOST_HOME/saturn/<name>/`. Mixins are gone; credentials under `$HOME/.ssh`, `$HOME/.claude.json`, `$HOME/.config/gh`, etc. come along for free with the `$HOME` mount.
- **New env var `SATURN_HOST_HOME`** carries the host-side `$HOME` into nested containers so the innermost saturn can still bind-mount from the real host path (the SATURN_HOST_SOCK pattern, extended).
- **`HOME=$SATURN_HOST_HOME`** injected inside so tools that look up `~` find the bind-mounted host home naturally.
- **Command surface shrinks.** `put`, `get`, `project config`, `runtime info`, `runtime init`, and the entire `--mixins` surface area are removed. `project new/rm/shell/ls` are hoisted to top-level as `new`/`rm`/`ls` (no `project shell`; its job — base-image access for scaffolding — is done by `saturn new` on the host).
- **Discovery switches from volumes to containers.** `saturn.volume=ws` labels are gone; `saturn.project=<name>` on containers replaces them. `ls` unions that with directory children of `$HOME/saturn/`.

## Consequences

- Much smaller script: ~350 lines vs ~950. The mixin registry + `_check_mount_overlap` + `ensure_volume`/`ensure_mixin_volume` + put/get helpers + runtime commands all delete.
- User-global state is just part of `$HOME`. If you want a mixin, `apt-get install` it in your project Containerfile and use the config the bind-mount brings along. No separate "set up auth once" step — the host already has it.
- Trust boundary is larger: the container can now read/write *anything* under `$HOME`, not just the `ws` volume. For a personal dev tool this is acceptable; for untrusted code it is not. Documented in the README and in [engine-socket.md](../boundaries/engine-socket.md).
- Host filesystem is no longer "zero-state". You will see `~/saturn/` with one directory per project. This is the explicit reversal of [0002](0002-volume-first-zero-host-state.md).
- Rootful engines still *work* but produce host-root-owned files inside the mount — the ergonomics are only good under rootless.

## Rejected alternatives

- **Keep named volumes, drop only the agent user.** Would remove the sudo/chown complexity but keep `put`/`get` and the mixin volumes. We'd have a non-obvious split where project content is in a volume but credentials come from host paths — harder to reason about than "everything under `$HOME` is available."
- **Bind-mount only specific subdirectories of `$HOME`** (a list of "mixin targets"). Keeps a smaller blast radius but reintroduces a registry-of-things-to-mount — exactly the mixin system we're collapsing.
- **Keep `IS_SANDBOX=1` as a runtime `-e` flag** rather than baking it into the base image. Works but is one more thing every project Containerfile would have to replicate if it was built FROM a different base; baking it into `saturn-base` makes derived images inherit it automatically.
