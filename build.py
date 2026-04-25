#!/usr/bin/env python3
"""Build the single-file `saturn` zipapp from `src/saturn/`.

Run from the repo root:

    python3 build.py            # writes ./saturn (overwrites)
    python3 build.py -o /tmp/x  # writes /tmp/x

The output is an executable zipapp with a `#!/usr/bin/env python3`
shebang. It IS the distribution artifact — `curl .../saturn -o
~/.local/bin/saturn && chmod +x` is the install. The base image's
`COPY saturn /usr/local/bin/saturn` step uses the same file.
"""

from __future__ import annotations

import argparse
import sys
import zipapp
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def _include(path: Path) -> bool:
    """Filter passed to zipapp.create_archive: skip __pycache__ + .pyc files."""
    parts = path.parts
    if "__pycache__" in parts:
        return False
    if path.suffix == ".pyc":
        return False
    return True


def build(out: Path) -> None:
    if not SRC.is_dir():
        sys.exit(f"package source not found: {SRC}")
    zipapp.create_archive(
        source=str(SRC),
        target=str(out),
        interpreter="/usr/bin/env python3",
        filter=_include,
    )
    out.chmod(0o755)
    print(f"built: {out}  ({out.stat().st_size} bytes)")


def main() -> None:
    p = argparse.ArgumentParser(description="Build the saturn zipapp.")
    p.add_argument("-o", "--output", default=str(ROOT / "saturn"),
                   help="output path (default: ./saturn)")
    args = p.parse_args()
    build(Path(args.output).resolve())


if __name__ == "__main__":
    main()
