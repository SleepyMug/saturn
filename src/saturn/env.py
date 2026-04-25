"""Module-level env derivation + engine probe.

`IS_HOST`, `SATURN_SOCK`, `BASE_IMAGE` are read at import (deterministic,
side-effect-free apart from re-exporting `SATURN_SOCK` and `DOCKER_HOST`
into `os.environ` so compose's env substitution sees them at config
time).

The cli/backend probe (`probe_engine`) is gated behind an explicit call
from `cli.main()` — keeps individual module imports cheap for tests and
defers any network/docker activity until we actually need it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BASE_IMAGE = os.environ.get("SATURN_BASE_IMAGE", "localhost/saturn-base:latest")

# Presence of SATURN_IN_GUEST means we're inside a saturn-launched
# container. The outer saturn sets it in the compose.yaml it generates.
IS_HOST = os.environ.get("SATURN_IN_GUEST") != "1"


def _default_socket() -> str:
    xdg = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    for cand in (f"{xdg}/podman/podman.sock", f"{xdg}/docker.sock", "/var/run/docker.sock"):
        if Path(cand).is_socket():
            return cand
    return f"{xdg}/podman/podman.sock"


# SATURN_SOCK is the socket path visible to *this* saturn process.
# - Host mode: an absolute host path (xdg runtime dir or /var/run).
# - Guest mode: /var/run/docker.sock (the outer saturn bind-mounts the
#   real host socket here).
# Exported back into the env so ${SATURN_SOCK} substitutes inside
# compose.yaml at `compose config` time.
SATURN_SOCK = os.environ.get("SATURN_SOCK") or (
    "/var/run/docker.sock" if not IS_HOST else _default_socket()
)
os.environ["SATURN_SOCK"] = SATURN_SOCK
os.environ["DOCKER_HOST"] = f"unix://{SATURN_SOCK}"


# Saturn's design assumes a rootless engine: container-uid 0 is mapped
# back to the invoking host user, so files written from inside land as
# that user on host. A root-owned socket means a rootful daemon (no
# userns remap), which breaks that assumption — files flip to root:root
# on host and the socket hands container-root full host-root. See
# docs/boundaries/engine-socket.md ("Rootless is load-bearing").
def _detect_cli() -> str:
    """Identify the `docker` binary on $PATH: real docker-cli vs podman shim.

    Returns "docker", "podman", or "unknown". The `podman-docker` package
    installs a `/usr/bin/docker` that execs podman, so `docker --version`
    reports the real banner. Cannot distinguish local podman from
    `podman --remote` — the banners are byte-identical; mismatches surface
    via the backend probe instead.
    """
    try:
        r = subprocess.run(
            ["docker", "--version"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if r.returncode != 0:
        return "unknown"
    s = r.stdout.lower()
    if "docker version" in s:
        return "docker"
    if "podman" in s:
        return "podman"
    return "unknown"


def _detect_backend() -> tuple[str, bool]:
    """Identify the engine behind $SATURN_SOCK + whether the socket is root-owned.

    Probes via plain `docker version` (no `--format`; JSON-template form
    fails under podman's own CLI because podman's version struct lacks
    `.Components`). Substring `"Podman Engine"` in stdout → podman;
    clean exit without that string → docker; any failure → unknown.

    Also prints the rootful-engine advisory on host when the socket is
    root-owned — consolidated from the previous inline check.
    """
    root_owned = False
    try:
        root_owned = Path(SATURN_SOCK).stat().st_uid == 0
    except OSError:
        pass
    if IS_HOST and root_owned and os.getuid() != 0:
        print(
            f"saturn: warning: {SATURN_SOCK} is root-owned (rootful engine). "
            f"Files written from inside will land as root on host; the socket "
            f"grants container-root host-root. See docs/boundaries/engine-socket.md.",
            file=sys.stderr,
        )
    try:
        r = subprocess.run(
            ["docker", "version"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", root_owned
    if r.returncode != 0:
        return "unknown", root_owned
    if "Podman Engine" in r.stdout:
        return "podman", root_owned
    return "docker", root_owned


def probe_engine() -> None:
    """Run cli/backend detection and set DOCKER_BUILDKIT adaptively.

    Skipped (and `DOCKER_BUILDKIT` defaults to "0") if
    `SATURN_SKIP_ENGINE_PROBE=1`. Exits hard on a podman-CLI ×
    non-podman-backend mismatch — the failure mode under compose is an
    opaque `ping response was 404`.
    """
    if os.environ.get("SATURN_SKIP_ENGINE_PROBE") == "1":
        os.environ.setdefault("DOCKER_BUILDKIT", "0")
        return
    cli = _detect_cli()
    backend, root_owned = _detect_backend()

    # Check A: podman CLI (incl. --remote) only speaks podman's native
    # REST API. Against a dockerd socket, the probe itself fails with
    # `ping response was 404` (returncode ≠ 0 → backend="unknown").
    # Healthy podman CLI + podman backend would return backend="podman".
    # So any non-"podman" result under CLI=podman is a mismatch — fail
    # fast with a crisper error than whatever compose would eventually
    # surface.
    if cli == "podman" and backend != "podman":
        sys.exit(
            f"saturn: podman CLI cannot talk to the engine at "
            f"{SATURN_SOCK} (expected podman backend, probe returned "
            f"{backend!r}). Point $SATURN_SOCK at a podman socket, or "
            f"install docker-cli."
        )

    # Check B: docker CLI + docker backend → let docker default win
    # (BuildKit on). The rootful gate only applies on host — inside a
    # guest, rootless-userns mapping makes the socket look root-owned
    # from Python's view even against a rootless backend, so trust the
    # backend probe there. Podman backend (or unknown) keeps the
    # classic-builder default because podman's docker-compat socket
    # doesn't serve the BuildKit API.
    if cli == "docker" and backend == "docker" and not (IS_HOST and root_owned):
        os.environ.pop("DOCKER_BUILDKIT", None)
    else:
        os.environ.setdefault("DOCKER_BUILDKIT", "0")
