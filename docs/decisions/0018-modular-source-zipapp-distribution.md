# 0018 — Modular source under `src/saturn/`, distributed as a zipapp

> Saturn's source moves from one ~670-line file into a `src/saturn/`
> package (`cli`, `env`, `workspace`, `base`, `engine`, `docker`).
> Distribution stays single-file via `python -m zipapp`: `build.py`
> assembles `src/` into the executable `./saturn` (still `curl |
> chmod +x`-installable). A new `saturn docker <args>` subcommand is a
> thin pass-through to the `docker` CLI for callers that need to drive
> the engine directly without compose.

## Context

The single-file `saturn` script crossed 670 lines as decisions
0014–0017 layered on the override chain, the cli/backend probe, and
the `--nesting` flag. Editing flow inside one file was getting noisy;
keeping the strict module-doc layout (`docs/components/{cli,workspace,
base-image,engine}/`) honest while everything actually lived in one
file was friction.

[Decision 0001](0001-single-file-distribution.md) explicitly rejected
zipapp as "overkill for a single Python script". With the source now
larger and naturally splittable along the lines the docs already
describe, that calculus flips: zipapp keeps the single-file
distribution while letting the source mirror its architecture
documentation.

A separate motivation: the [issue's task description](
../../../) — apps building on saturn want to manipulate containers
directly (e.g. `docker exec saturn_foo bash`, `docker logs saturn_bar`)
without re-deriving saturn's `DOCKER_HOST` / engine selection. The
compose pass-through already covers compose-shaped operations; nothing
covers raw docker. A flat `saturn docker <args>` shim is the minimal
surface.

## Decision

### Module split

```
src/
  __main__.py          # zipapp entry — imports saturn.cli.main
  saturn/
    __init__.py
    __main__.py        # `python -m saturn` entry — same dispatch
    env.py             # IS_HOST / SATURN_SOCK / BASE_IMAGE +
                       #   probe_engine() (cli vs podman, BUILDKIT)
    workspace.py       # cmd_new, find_workspace, normalize_name,
                       #   templates + _AUTOCREATE
    base.py            # BASE_DOCKERFILE, _build_base, cmd_base_*,
                       #   materialize_script (zipapp/source dual)
    engine.py          # _run, _translate, _translate_compose,
                       #   _current_container_mounts, _find_overrides,
                       #   passthrough, cmd_host_addr
    docker.py          # cmd_docker — `saturn docker <args>` shim
    cli.py             # main(), help, argv switch
```

The split mirrors the existing component docs verbatim — one Python
module per `docs/components/<name>/` subdir, with `cli` glue as a
seventh module.

### Engine probe gated behind `probe_engine()`

The cli/backend probe (`_detect_cli`, `_detect_backend`) used to fire
at module import. With imports now finer-grained (a unit test for
`workspace.py` shouldn't shell out to `docker --version`), the probe
moves behind `env.probe_engine()` and is called once from `cli.main()`
before dispatch. Behavior is preserved on the production path; tests
that import `saturn.workspace` etc. don't trigger any subprocess
calls.

### Single-file distribution via zipapp

`build.py` at repo root runs `zipapp.create_archive(source='src',
target='saturn', interpreter='/usr/bin/env python3')`. Output is the
familiar standalone executable — same install path
(`curl .../saturn -o ~/.local/bin/saturn && chmod +x`), same
`COPY saturn /usr/local/bin/saturn` step in the base image's
Dockerfile. The zipapp file IS the distribution; the `src/` tree is
build-time-only.

### `materialize_script(dest)` handles both invocation forms

The base-image build needs a single-file saturn binary in its build
context. With the package source split, `shutil.copy(SCRIPT, dst)`
no longer covers the source-tree case. `base.materialize_script`:

- Zipapp invocation: `sys.argv[0]` is the zipapp file (real file on
  disk) → `shutil.copy(argv0, dst)`.
- Source invocation (`python -m saturn` from a checkout): build a
  fresh zipapp from `src/` into `dst` via
  `zipapp.create_archive(source=src_dir, target=dst, …)`.

Either way, the base image's `COPY saturn /usr/local/bin/saturn` step
sees a single executable file at the build context root.

### `saturn docker <args>` subcommand

A new top-level subcommand in `cli.main`'s switch:

- `argv[0] == "docker"` → `cmd_docker(argv[1:])` →
  `subprocess.run(["docker", *args])` and exit with its returncode.
- Empty argv after `docker` prints `usage: saturn docker <args>` and
  exits 2.
- No argparse: every flag is forwarded to `docker` verbatim, so saturn
  doesn't intercept `--help`, `-f`, etc.

This shim adds value because saturn already resolved
`DOCKER_HOST=unix://$SATURN_SOCK` and adapted `DOCKER_BUILDKIT` based
on the cli/backend probe. Callers that drive saturn from outside
(galaxy-mini agent harnesses, CI scripts) get one consistent entry
point that hits the same engine compose would.

## Consequences

- **Source files mirror docs.** Editing the engine pipeline opens
  `engine.py`; the matching doc is `docs/components/engine/`. Tests
  per-module follow the same axis.
- **Distribution shape is unchanged.** Users still curl one file. The
  zipapp adds ~100 bytes of zip header and a `__main__.py` shim — the
  install pattern, the base-image `COPY` step, the executable bit are
  all the same.
- **`saturn docker` is the third pass-through tier.** Pure compose
  operations go through the compose pass-through (`saturn up`,
  `saturn ps`); engine operations go through `saturn docker` (`saturn
  docker exec saturn_foo bash`); saturn-specific commands (`new`,
  `base`, `shell`, `host-addr`) keep their own handlers. Each tier has
  a clear inclusion criterion.
- **Test surface is sane.** Each module is imported standalone; the
  engine probe is opt-in. `tests/` contains unit tests for
  workspace/engine/docker plus an end-to-end zipapp build/run test.
  Total runtime well under a second.
- **Build step before commit.** `./saturn` is a build artifact; CI (or
  a developer) must run `python3 build.py` before committing source
  changes. Documented in the README. Out-of-date `saturn` is a
  reviewer-noticeable diff because the zipapp byte-content changes.
- **Decision 0001's rejection reverses.** That decision noted "zipapp
  bundle — overkill for a single Python script". With the script no
  longer single-file, the rejection no longer applies; we land where
  0001 said to revisit.

## Rejected alternatives

- **Keep one file, accept the size.** Editor experience continues to
  degrade as decisions accumulate. The doc layout already implies
  modules; aligning the source with the docs is overdue.
- **Modular source with no zipapp; ship a `saturn` directory.** Would
  break the `curl one-file && chmod +x` install promise — the most
  user-friendly distribution shape saturn has. The zipapp gets us both.
- **Use a build tool (setuptools, pyproject + entrypoints) instead of
  `zipapp`.** Adds packaging metadata, install steps, and a
  per-environment shim. `python -m zipapp` is stdlib, single-step,
  produces an executable file with no install required. The fancier
  story isn't justified for a 600-line wrapper.
- **`saturn engine <args>` instead of `saturn docker <args>`.** The
  underlying CLI on `$PATH` is named `docker` (even when it's a podman
  shim — see [decision 0016](0016-adaptive-buildkit-and-cli-backend-checks.md)).
  Naming the subcommand after the binary it runs is the least
  surprising option.
- **Wire `saturn docker` through the `_run` helper.** `_run` defaults
  to `check=True` and is opinionated about capture; `saturn docker`
  needs to forward stdin/stdout/stderr verbatim and propagate the
  child's exit code. Keep the call site flat.
- **Add a `saturn podman <args>` sibling.** Saturn already targets
  the `docker` CLI exclusively (the podman case is covered via the
  `podman-docker` shim). A second sibling subcommand would split the
  surface for no user benefit.
