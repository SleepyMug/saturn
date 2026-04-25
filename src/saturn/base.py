"""Base image build (`saturn base ...`).

Inlined Dockerfile produces `localhost/saturn-base:latest`. The build
context is assembled in a tempdir with a copy of the running saturn
binary so the `COPY saturn /usr/local/bin/saturn` step works wherever
saturn is invoked from.

`materialize_script` handles two invocation forms:
  - zipapp: `sys.argv[0]` is the zipapp file (single, real file on
    disk) — copy it directly.
  - source: `python -m saturn` from a checkout — build a fresh zipapp
    on the fly from this package.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipapp
from pathlib import Path

from .env import BASE_IMAGE
from .engine import _run


BASE_DOCKERFILE = """\
FROM docker.io/library/debian:trixie-slim

RUN apt-get update \\
 && apt-get install -y --no-install-recommends \\
      docker-cli docker-compose ca-certificates python3 git curl \\
 && rm -rf /var/lib/apt/lists/*

COPY saturn /usr/local/bin/saturn
RUN chmod 0755 /usr/local/bin/saturn

ENV IS_SANDBOX=1

CMD ["sleep", "infinity"]
"""


def materialize_script(dest: Path) -> None:
    """Place a runnable single-file saturn binary at `dest`.

    Zipapp invocation: copy `sys.argv[0]` (the zipapp file). Source
    invocation: build a zipapp from this package into `dest`. In both
    cases the result is a single executable file the base image's
    Dockerfile can `COPY` into `/usr/local/bin/saturn`.
    """
    argv0 = Path(sys.argv[0]).resolve()
    if argv0.is_file():
        shutil.copy(argv0, dest)
        dest.chmod(0o755)
        return
    # Source invocation (e.g. `python -m saturn`). __file__ is
    # `<repo>/src/saturn/base.py`; the zipapp source root is `<repo>/src/`,
    # which contains both this package and the top-level __main__.py.
    src_dir = Path(__file__).resolve().parent.parent
    if not (src_dir / "__main__.py").is_file():
        sys.exit(
            "saturn: cannot locate package source to build a saturn binary "
            "for the base image (sys.argv[0] is not a file and src/__main__.py "
            "is missing)."
        )
    zipapp.create_archive(
        source=str(src_dir),
        target=str(dest),
        interpreter="/usr/bin/env python3",
    )
    dest.chmod(0o755)


def _build_base(dockerfile_text: str) -> None:
    with tempfile.TemporaryDirectory(prefix="saturn-base-") as td:
        tdp = Path(td)
        (tdp / "Dockerfile").write_text(dockerfile_text)
        materialize_script(tdp / "saturn")
        _run("docker", "build", "-f", str(tdp / "Dockerfile"), "-t", BASE_IMAGE, td)


def cmd_base_default(_args: argparse.Namespace) -> None:
    _run("docker", "rmi", BASE_IMAGE, check=False, capture=True)  # quiet if absent
    print(f"building base image: {BASE_IMAGE}")
    _build_base(BASE_DOCKERFILE)
    print(f"base ready: {BASE_IMAGE}")


def cmd_base_build(args: argparse.Namespace) -> None:
    path = Path(args.file)
    if not path.is_file():
        sys.exit(f"Dockerfile not found: {path}")
    _run("docker", "rmi", BASE_IMAGE, check=False, capture=True)
    print(f"building base image: {BASE_IMAGE} from {path}")
    _build_base(path.read_text())
    print(f"base ready: {BASE_IMAGE}")
