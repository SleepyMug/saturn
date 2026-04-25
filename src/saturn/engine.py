"""Compose translation pipeline + pass-through.

Resolves `.saturn/compose.yaml` (+ overrides) via `docker compose
config --format json`, in guest mode pre-builds any service `build:`
contexts and reverse-translates bind-mount sources via the current
container's `.Mounts` list, writes `.saturn/compose.json`, and forwards
to a second `docker compose` invocation.

Also hosts `cmd_host_addr` (one-line "where's the host" helper) and
the shared `_run` subprocess wrapper used by every other module.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from .env import IS_HOST
from .workspace import find_workspace, normalize_name


# ---------- subprocess wrapper ------------------------------------------

def _run(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    kwargs: dict = {"check": check}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
        kwargs["text"] = True
    return subprocess.run(list(args), **kwargs)


# ---------- host-addr ---------------------------------------------------

def cmd_host_addr() -> None:
    """Print the address to reach the host from the current context."""
    print("localhost" if IS_HOST else "host.docker.internal")


# ---------- translate + pass-through ------------------------------------

def _current_container_mounts() -> list[dict]:
    """Guest-mode: return the current container's bind mounts via engine inspect.

    The container's hostname (default: short container id, set by docker/
    compose at create time) is the inspect target. Read via
    `socket.gethostname()` rather than `$HOSTNAME` — the env var is a
    shell convention that isn't exported into every child process.
    """
    hn = socket.gethostname().strip()
    if not hn:
        sys.exit("could not determine container hostname — cannot self-inspect")
    r = _run("docker", "inspect", "--format", "{{json .Mounts}}", hn,
             check=False, capture=True)
    if r.returncode != 0:
        sys.exit(
            f"docker inspect {hn} failed: {r.stderr.strip()}\n"
            f"(saturn self-inspects by hostname. If compose.yaml sets `hostname:`,\n"
            f" remove it — compose then defaults hostname to the short container id.)"
        )
    return json.loads(r.stdout)


def _translate(source: str, mounts: list[dict]) -> str | None:
    """Resolve an inside-path source to its host backing via the mount list.

    Matches the longest bind-mount Destination that is an ancestor of
    `source`. Returns None if nothing matches.
    """
    src = Path(source)
    binds = [m for m in mounts if m.get("Type") == "bind"]
    # Longest-destination-first.
    for m in sorted(binds, key=lambda m: -len(m.get("Destination", ""))):
        dst = Path(m["Destination"])
        try:
            rel = src.relative_to(dst)
        except ValueError:
            continue
        host = Path(m["Source"])
        return str(host if str(rel) == "." else host / rel)
    return None


def _find_overrides(ws: Path) -> list[Path]:
    """Return override compose files to layer onto `.saturn/compose.yaml`.

    Two sources, in order:
      1) `.saturn/compose.override*.yaml` globbed and sorted lexically.
         Docker-compose convention generalized (compose itself auto-picks
         up a single `compose.override.yaml`); we accept any number to
         let callers stack their own.
      2) `SATURN_COMPOSE_OVERRIDES` env var, colon-separated absolute
         paths — the programmatic hook for orchestrators that write
         transient overrides.

    Returns an empty list when nothing matches.
    """
    overrides = sorted((ws / ".saturn").glob("compose.override*.yaml"))
    env_val = os.environ.get("SATURN_COMPOSE_OVERRIDES", "")
    overrides += [Path(p).resolve() for p in env_val.split(":") if p]
    return overrides


def _translate_compose(compose_files: list[Path], project: str) -> Path:
    """Run `compose config --format json`, handle guest-mode translation, write compose.json.

    Accepts one or more compose files; docker compose merges them with its
    native `-f a -f b` semantics (later files overlay earlier ones). The
    resulting merged spec is what gets nested-path-translated.

    Host mode: the spec is written back unchanged after compose resolves it
    (env substitution, relative-path resolution). Safe round-trip.

    Guest mode: two problems to solve.
      1) Bind-mount sources are inside-paths (from the compose client's
         view) but the daemon resolves paths on the host. Translate via
         engine-inspect of the current container's .Mounts list.
      2) build.context is read by the compose *client* off its local
         filesystem, then streamed to the daemon. In guest mode the
         client can see inside-paths but can't see host-paths, so we
         cannot translate build.context — we must build the service
         image here (with the inside-path context) and then strip the
         build: stanza so compose doesn't re-read it.
    """
    cmd = ["docker", "compose"]
    for f in compose_files:
        cmd += ["-f", str(f)]
    cmd += ["-p", project, "config", "--format", "json"]
    r = _run(*cmd, check=False, capture=True)
    if r.returncode != 0:
        sys.exit(f"docker compose config failed:\n{r.stderr}")
    spec = json.loads(r.stdout)

    if not IS_HOST:
        mounts = _current_container_mounts()

        # Step 1: pre-build any service that declares `build:` — using the
        # inside-path context, so the client can read it. Strip `build:`
        # from the spec afterwards. The image must have a name for
        # compose to reference post-strip; compose config already fills
        # in a default `image:` when the user didn't specify one.
        for svc_name, svc in (spec.get("services") or {}).items():
            build = svc.get("build")
            if not isinstance(build, dict):
                continue
            ctx = build.get("context")
            if not ctx:
                continue
            image = svc.get("image")
            if not image:
                sys.exit(
                    f"service {svc_name!r} has build: but no image: — "
                    f"saturn needs an image tag to reference post-build."
                )
            dockerfile = build.get("dockerfile", "Dockerfile")
            print(f"building {image} from {ctx}...")
            _run("docker", "build", "-f", f"{ctx}/{dockerfile}", "-t", image, ctx)
            svc.pop("build", None)

        # Step 2: translate every remaining bind-mount source from
        # inside-path to host-path using the current container's mounts.
        unresolved: list[str] = []
        for svc_name, svc in (spec.get("services") or {}).items():
            for vol in svc.get("volumes") or []:
                if vol.get("type") != "bind":
                    continue
                new_src = _translate(vol["source"], mounts)
                if new_src is None:
                    unresolved.append(f"{svc_name}.volumes: {vol['source']}")
                else:
                    vol["source"] = new_src
        if unresolved:
            lines = "\n".join(f"  {u}" for u in unresolved)
            sys.exit(
                "bind-mount source(s) not under any mount of the current container:\n"
                + lines
                + "\n(Inside a saturn container, every compose bind source must live "
                  "under an existing mount — workspace, socket, or another mounted path.)"
            )

    out = compose_files[0].parent / "compose.json"
    out.write_text(json.dumps(spec, indent=2))
    return out


def passthrough(argv: list[str]) -> None:
    ws = find_workspace()
    project = normalize_name(ws.name)
    compose_files = [ws / ".saturn" / "compose.yaml", *_find_overrides(ws)]
    compose_json = _translate_compose(compose_files, project)

    cmd = ["docker", "compose", "-f", str(compose_json), "-p", project, *argv]
    r = subprocess.run(cmd)
    # Banner is for non-obvious compose failures. Suppress when the non-zero
    # came from the user (SIGINT = 130) or from a child process whose exit
    # compose forwards verbatim (`exec`, `run`) — e.g. bash exiting with the
    # last command's status on Ctrl-D out of `saturn shell`.
    if r.returncode not in (0, 130) and argv[0] not in ("exec", "run"):
        print(f"\n(docker compose exited {r.returncode} — ran: {' '.join(cmd)})",
              file=sys.stderr)
    sys.exit(r.returncode)
