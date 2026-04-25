"""`python -m saturn` entry (development invocation from a checkout).

The zipapp uses `src/__main__.py` instead, which sits next to (not
inside) the `saturn/` package so relative imports resolve at archive
root. Both files dispatch into the same `saturn.cli.main`.
"""

from __future__ import annotations

import subprocess
import sys

from .cli import main


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        sys.exit(130)
