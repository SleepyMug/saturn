"""Direct `docker` CLI pass-through.

`saturn docker <args>` forwards verbatim to the `docker` binary on
$PATH with saturn's already-resolved env (DOCKER_HOST pointing at
$SATURN_SOCK, DOCKER_BUILDKIT picked by the engine probe). Lets apps
that drive saturn manipulate containers/images/networks without
threading their own backend selection.

This is intentionally argparse-free — every flag goes through to
`docker`. Returncode is forwarded.
"""

from __future__ import annotations

import subprocess
import sys


def cmd_docker(argv: list[str]) -> None:
    """Forward `argv` to `docker` and exit with its returncode.

    `argv` is `sys.argv[2:]` from the main dispatcher (everything after
    `saturn docker`). Empty argv prints a usage line and exits 2.
    """
    if not argv:
        print("usage: saturn docker <args>", file=sys.stderr)
        sys.exit(2)
    r = subprocess.run(["docker", *argv])
    sys.exit(r.returncode)
