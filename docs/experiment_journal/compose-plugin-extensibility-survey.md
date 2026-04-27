# Compose plugin/extension extensibility — feasibility survey

> Dated: 2026-04-27. Issue [#2](https://github.com/galaxy-mini/saturn/issues/2). Surveys whether saturn could be re-expressed as a docker / podman compose plugin or extension. **Result: the saturn-specific commands (`new`, `base`, `shell`, `host-addr`, `docker`) could be repackaged as a docker CLI plugin (`docker saturn …`); the compose-spec translation pipeline — saturn's actual value-add — has no compose extension point that could host it. The wrapper shape is load-bearing.**

## Question

Saturn today is a wrapper: `saturn <argv>` runs `docker compose config --format json`, post-processes the spec (reverse mount lookup in guest mode, pre-build, override merge), and forwards to a second `docker compose -f <translated>.json -p <ws> <argv>`. Could those steps run as a docker compose / podman compose plugin or extension instead, leaving the user typing `docker compose up` directly? What can be built into the upstream tools? What cannot?

## Inventory of compose-side extension surfaces (2026-04)

### Docker CLI plugins (`~/.docker/cli-plugins/docker-<name>`)

The CLI walks `~/.docker/cli-plugins`, then `CLIPluginsExtraDirs` from `config.json`, then `/usr/libexec/docker/cli-plugins`, `/usr/local/lib/docker/cli-plugins`. Earlier entries shadow later ones. Plugin names matching `^[a-z][a-z0-9]*$` that *don't* collide with built-in commands are accepted; collisions with built-ins (incl. `compose`, `build`, `run`) are **rejected** — a plugin cannot replace or override a core command.

Discovery contract: the CLI execs `docker-<name> docker-cli-plugin-metadata`; the plugin replies with one JSON document on stdout:

```json
{ "SchemaVersion": "0.1.0", "Vendor": "...", "Version": "...",
  "ShortDescription": "...", "URL": "...", "Hidden": false }
```

Subsequent invocations (`docker <name> <args…>`) re-exec the plugin with the args plus a set of `DOCKER_CLI_PLUGIN_*` env vars. `docker compose` and `docker buildx` are themselves the canonical examples — `docker-compose` and `docker-buildx` binaries shipped under `/usr/libexec/docker/cli-plugins`.

Sources: [`cli/cli-plugins/manager`](https://pkg.go.dev/github.com/docker/cli/cli-plugins/manager), [`cli-plugins/plugin/plugin.go`](https://github.com/docker/cli/blob/master/cli-plugins/plugin/plugin.go), [`cli-plugins/metadata/metadata.go`](https://github.com/docker/cli/blob/master/cli-plugins/metadata/metadata.go).

### Docker CLI hooks (post-invocation advisory)

Configured **not** in plugin metadata but in `~/.docker/config.json`:

```json
{ "plugins": { "ai": { "hooks": "run", "error-hooks": "build,compose,pull" } } }
```

`hooks` runs after every invocation of the listed commands; `error-hooks` only on non-zero exit. The CLI invokes `docker-<name> cli-plugin-hooks <command-string>`; the plugin returns templated text the CLI prints below the wrapped command's output. **Notification only — cannot pre-process arguments, mutate stdin/stdout, or change exit codes.** [PR #6794 (error-hooks)](https://github.com/docker/cli/pull/6794), [`docker-config-json.5.md`](https://github.com/docker/cli/blob/master/man/docker-config-json.5.md).

### Compose `provider:` services (v2.36.0, May 2025)

The most plugin-shaped surface compose has. A service may declare:

```yaml
services:
  db:
    provider:
      type: awesomecloud   # docker CLI plugin name OR PATH binary
      options: { type: mysql, size: small }
```

Compose execs `<type> compose --project-name <P> up [flags-from-options]` on `up`, `<type> compose --project-name <P> down <service>` on `down`. The provider speaks back over **stdout, JSON-line-delimited**:

```json
{"type":"info","message":"…"}
{"type":"setenv","message":"URL=mysql://…"}
{"type":"error","message":"…"}
```

`setenv` lines are exposed as `<SERVICE>_<KEY>` env vars to dependents. **Per-service resource handler — not a project-level pre-processor.** No `before-config` / `after-config` / `before-up` callbacks. [docs/extension.md](https://github.com/docker/compose/blob/main/docs/extension.md), [provider-services docs](https://docs.docker.com/compose/how-tos/provider-services/), [v2.36.0 release](https://github.com/docker/compose/releases/tag/v2.36.0). `docker model` is the in-tree exemplar: ships as a CLI plugin AND registers as a provider.

### Compose service `lifecycle:` (v2.30.0, Oct 2024)

`services.<svc>.post_start` and `pre_stop` hooks execute **inside the container** after start / before stop, optionally `privileged: true`, optionally as a different `user:`. They do not run on the host and do not run during `docker compose run` ([issue #13593](https://github.com/docker/compose/issues/13593)). [lifecycle docs](https://docs.docker.com/compose/how-tos/lifecycle/). Cannot be used to translate the compose model.

### `x-` extension fields

[compose-spec/11-extension.md](https://github.com/compose-spec/compose-spec/blob/main/11-extension.md): "Compose ignores any fields that start with `x-`, this is the sole exception where Compose silently ignores unrecognized fields." May appear at the top level and inside any map. **Engine never calls out based on `x-…`.** Vendors attach their own behavior (e.g. `x-podman.in_pod`, `x-podman.rootful` are honored by `podman-compose`); compose v2 does not. Useful as anchor plumbing (`x-common: &common …`) and as a place for tools-on-top to stash typed metadata (e.g. saturn's own draft `x-saturn.autocreate` in [plans/bind-mount-source-validation.md](../plans/bind-mount-source-validation.md)).

### `include:` / `extends:` / merge (`-f a -f b`)

[14-include.md](https://github.com/compose-spec/compose-spec/blob/main/14-include.md): each included file is parsed and **interpolated in its own scope** (its own `.env`, relative paths against its dir), then the resulting model merges into the parent. [13-merge.md](https://github.com/compose-spec/compose-spec/blob/main/13-merge.md): scalars overwrite, sequences append, mappings deep-merge per-attribute. `extends:` is recursive same-file or cross-file service inheritance ([extends docs](https://docs.docker.com/compose/how-tos/multiple-compose-files/extends/)); resources (`volumes`/`networks`/`secrets`) are not inherited.

These are file-merge primitives, not extension points. Saturn's override chain (`compose.override*.yaml` glob + `SATURN_COMPOSE_OVERRIDES`) already builds on `-f` merge; `include:` is the in-spec route to the same effect but doesn't run host-side code.

### Compose Go SDK (`pkg/api`)

[`pkg.go.dev/.../pkg/api`](https://pkg.go.dev/github.com/docker/compose/v2/pkg/api), [Compose SDK docs](https://docs.docker.com/compose/compose-sdk/). `compose.NewComposeService()` + `LoadProject()` lets a Go binary load a project, mutate the in-memory model, and call `Up`/`Down`/`Run`/etc. without shelling out. **Library, not a plugin contract** — anyone using it is building a sibling tool that re-implements compose's CLI surface, not extending compose itself.

### Long-standing host-side hook requests

[#1341 "Hooks/Plugins to run arbitrary scripts"](https://github.com/docker/compose/issues/1341) and [#6736 "Hooks to run scripts on host before starting any containers"](https://github.com/docker/compose/issues/6736) — both ~10 years old, both still open. Compose v2 has not added a host-side `before-up` / `after-config` hook.

### `podman-compose` (Python)

[containers/podman-compose](https://github.com/containers/podman-compose). Single Python script with an `@cmd_run` decorator registry and an internal `normalize_service` / `flat_deps` / `rec_deps` transformation pipeline — **none of these are exposed as a public plugin API**. No setuptools entry-point, no config-file callout. Forking or monkey-patching at import time is the only extension path. Honors the `x-podman` namespace per-service (`in_pod`, `rootful`, `podman_args`, etc.); the v2 `lifecycle` keys are not implemented as of [#1303](https://github.com/containers/podman-compose/issues/1303).

### `podman compose` subcommand (Go shim)

[podman-compose.1](https://docs.podman.io/en/latest/markdown/podman-compose.1.html). Iterates `compose_providers` from `containers.conf` (default `["docker-compose", "podman-compose"]`, override with `PODMAN_COMPOSE_PROVIDER`), starts the podman API socket if not running, sets `DOCKER_HOST=unix://<podman-socket>`, execs the chosen provider with argv unchanged. **Pure env+exec shim. No transformation or hook surface.**

### OCI hooks (`prestart`/`createRuntime`/`createContainer`/…)

[`oci-hooks(5)`](https://manpages.debian.org/unstable/podman/oci-hooks.5.en.html). JSON files in `/usr/share/containers/oci/hooks.d`, `/etc/containers/oci/hooks.d`, `~/.config/containers/oci/hooks.d`. Run by the **OCI runtime**, one container at a time, *after* the OCI bundle is constructed. Cannot translate bind-mount sources before the runtime sees them; not aware of compose-level concepts (multi-service ordering, project name, image pre-build).

### Adjacent tools (how they compose with compose)

- **VS Code Dev Containers** ([containers.dev/implementors/json_reference](https://containers.dev/implementors/json_reference/)): reads `devcontainer.json`'s `dockerComposeFile` (string or list, merged via `-f`), `service`, `runServices`, `initializeCommand` (host-side, before compose up), `onCreate`/`postCreate`/`postStart`/`postAttachCommand` (in-container). Implemented by `@devcontainers/cli` Node, **not** as a docker CLI plugin — it shells out to `docker compose -f … up` itself. Closest analogue to saturn's pre-process step is `initializeCommand` — but devcontainers achieves it by being a sibling tool, not by hooking compose.
- **Dagger / Earthly** — replace compose entirely; not plugins.
- **`docker model`** — CLI plugin AND compose `provider:` registrant; the cleanest in-tree double-hatting example.
- No widely-used tool ships saturn-style "rewrite the compose model and shell out to compose" as a `~/.docker/cli-plugins/` plugin.

## Saturn feature → extension surface mapping

| Saturn feature | Mapped surface | Result |
|---|---|---|
| `saturn new --<flag>` (template seeding) | Sibling docker CLI plugin (`docker saturn new …`) | Works. Same code, different invocation entry point. |
| `saturn base default` / `base build` (build base image) | Sibling docker CLI plugin | Works. |
| `saturn shell` (alias to `exec dev bash`) | Sibling docker CLI plugin or shell alias; cannot be a compose `provider:` | Works as a sibling. |
| `saturn host-addr` | Sibling docker CLI plugin | Works. |
| `saturn docker <args>` (verbatim shim w/ `DOCKER_HOST` resolved) | Sibling docker CLI plugin | Works. |
| Workspace discovery (walk cwd up for `.saturn/compose.yaml`) | Any host-side tool can do it | Works. |
| Project name = workspace basename (`-p <name>`) | Sibling tool sets it; compose itself can't be told to derive it | Saturn-tool side. |
| Override chain (glob `compose.override*.yaml` + `SATURN_COMPOSE_OVERRIDES`) | `-f` merge or `include:` already handles arbitrary file lists; the *glob discovery* is saturn-tool side | Could be partly upstreamed by emitting a derived `COMPOSE_FILE` value; the sub-globbing is still saturn's. |
| **Compose-spec translation** — `docker compose config --format json` → post-process → write `compose.json` | None | **No compose extension point runs before compose loads its spec.** Cannot be done as a plugin. |
| **Reverse mount lookup** — translate every bind source from inside-path to host-path via `docker inspect <self>.Mounts` | None | **No surface exists.** OCI hooks run too late (OCI bundle already built); compose's `lifecycle` runs in-container; `provider:` is per-service, can't rewrite siblings; `x-` is passive; `include:` interpolates in includee's own scope but never calls out. |
| **Pre-build in guest mode** — run `docker build` from inside-path context, then strip `build:` so compose uses the produced image | None | **No surface exists.** Compose v2 has no `before-build`/`before-up` host hook. The tool that does the pre-build also has to mutate the spec compose then loads — only achievable by wrapping compose. |
| Adaptive `DOCKER_BUILDKIT` (cli/backend probe) | Could go in a sibling plugin's own process; but it must affect the env compose runs in | A CLI plugin sets env for **its own subprocess**, not the parent docker process. Saturn-as-plugin would have to invoke compose itself (i.e. wrap), reintroducing the wrapper. |
| Engine fail-fast (podman-CLI × docker-backend mismatch) | Same as above | Wrapper-only. |

The pivotal rows are the three that map to **None**. They share a structural property: each runs on the host **before** compose parses the spec, and each *mutates the spec compose then loads*. Compose's extension surfaces (`provider:`, `lifecycle`, `x-`, hooks, OCI hooks) are all either passive (compose ignores), in-container (run after compose has handed off to the runtime), or post-invocation (advisory text). There is no "preprocessor" slot.

The two structurally hardest features are reverse mount lookup and guest-mode pre-build. Both are guest-mode-only, both depend on a self-introspection step (`docker inspect <self>`) and a YAML rewrite. Even if compose grew a `before-config` hook tomorrow, a hook running on the host couldn't see "the inside paths of the *child* invocation that's about to happen" without the wrapping tool already in place to compute them — i.e. saturn would still need to construct the spec; the hook would just be a different invocation path for the same code.

## What a saturn-as-plugin shape would actually look like

If we abandon the goal of "user types `docker compose up`" and accept "user types `docker saturn up`":

1. Rename the zipapp `docker-saturn` and drop it under `~/.docker/cli-plugins/`. Add the `docker-cli-plugin-metadata` subcommand.
2. Keep everything else identical: `cli.main()` already dispatches on argv[1]. The wrapping of `docker compose` doesn't change — saturn-as-plugin still shells out to `docker compose -f compose.json -p <ws> <argv>` after translation.
3. Net change: `saturn up` becomes `docker saturn up`. Discovery and PATH changes; no architectural shift.
4. Lost: the standalone `./saturn` install ([decision 0001](../decisions/0001-single-file-distribution.md), reaffirmed in [0018](../decisions/0018-modular-source-zipapp-distribution.md)). Plugins must live under `~/.docker/cli-plugins/`. (Both can coexist by symlinking, but the docs and `curl` install would need to stop being the primary path.)

This isn't a migration into compose; it's a re-skinning under docker's plugin namespace. The translation pipeline still wraps compose. The user-visible difference is the prefix.

If we instead want the user to type **literal** `docker compose up` and have saturn participate transparently:

- Not achievable. Plugins cannot shadow `compose`. Hooks fire only after the fact and cannot rewrite the spec. Provider services are per-service handlers, not project transforms. There is no `before-config` or `before-up` host hook, and the [10-year-old issues asking for one](https://github.com/docker/compose/issues/1341) are still open.

The escape hatch is the **Compose Go SDK**. A from-scratch reimplementation could embed `compose-go`'s loader + the v2 service API, run translation in-process, and call `Up`/`Down`/etc. directly. That ships saturn as a sibling binary that *replaces* `docker compose` in the user's flow rather than wrapping it. It removes the second `docker compose config` call (saturn would own loading) at the cost of a Go rewrite, a vendored `compose-go` dep, and tracking compose v2's internal API — which is not stability-promised.

## Implications for saturn

- **The wrapper shape is load-bearing, not incidental.** The translation pipeline cannot be expressed as a plugin or extension of either docker compose or podman compose. There is no host-side preprocessor surface in either tool, and the long-running upstream issues asking for one have not moved.
- **Repackaging as a docker CLI plugin is a cosmetic change.** It changes the invocation prefix and the install path; it does not let any saturn code move into upstream. Probably not worth the loss of the curl-one-file install (decision 0001) for a `docker saturn up` rename.
- **`x-saturn` is the right namespace for typed saturn metadata in user compose files.** Already the design direction in [plans/bind-mount-source-validation.md](../plans/bind-mount-source-validation.md). The survey reinforces it: `x-` is the only spec-blessed slot for tool-specific data, and compose's "silently ignore" guarantee is durable.
- **No saturn change is warranted by this survey.** The conclusion is "stay the wrapper". Future work that *could* be informed by this:
  - If compose ever lands a `before-config` host hook (issues #1341 / #6736), revisit reverse mount lookup as a hook implementation.
  - If we want `docker saturn …` as a secondary install path (alongside the standalone `saturn` zipapp), the CLI-plugin protocol is well-documented and the metadata stub is small. This is purely additive.
  - If saturn ever needs to register a *resource* (e.g. an automatically-provisioned credential bind via a service block), compose's `provider:` is the right target — we'd ship saturn as a CLI plugin and have user compose files reference `provider: { type: saturn, options: {…} }`. Speculative; no current need.

## What this survey does *not* cover

- Replacing the `docker compose config --format json` step with a direct compose-go embed (Go rewrite). Out of scope for "plugin survey"; would warrant its own decision doc if pursued.
- Merging saturn's translation into compose-spec itself (e.g. "compose-spec adds an `x-translate-bind-mounts-from-host-mounts: true` directive that the engine honors"). Theoretically possible to draft as a spec proposal but unlikely to land — the use case is narrow (nested-container dev) and the implementation requires self-inspection that compose-spec has been careful to keep out of the file format.
- Whether to publish saturn under a third-party plugin index (Docker Extensions Marketplace, etc.) once a plugin shape exists. Distribution question, separate from feasibility.
