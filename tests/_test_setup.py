"""Test setup: import the in-tree package without building the zipapp.

Adds `src/` to `sys.path` so `import saturn` resolves to the source
package. Sets `SATURN_SKIP_ENGINE_PROBE=1` so any test that imports
`saturn.cli` and triggers `probe_engine()` doesn't shell out to docker.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


os.environ.setdefault("SATURN_SKIP_ENGINE_PROBE", "1")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
