"""Zipapp entry point.

Sits at the archive root next to the `saturn/` package so relative
imports inside the package resolve normally. The build script
(`build.py`) zips up `src/` as a whole; running `./saturn` (the
zipapp file) loads this `__main__` first, which dispatches to
`saturn.cli.main`.
"""

from __future__ import annotations

import subprocess
import sys

from saturn.cli import main


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        sys.exit(130)
