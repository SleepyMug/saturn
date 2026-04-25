"""saturn: compose-native dev-container wrapper with nested path translation.

Distributed as a single-file zipapp built from this package by `build.py`.
Source is split across:

  env.py        — IS_HOST / SATURN_SOCK / BASE_IMAGE constants + engine probe
  workspace.py  — `cmd_new`, `find_workspace`, `normalize_name`, templates
  base.py       — base-image build (`cmd_base_*`, inlined Dockerfile)
  engine.py     — translate-and-pass-through pipeline + compose helpers
  docker.py     — `saturn docker <args>` direct CLI pass-through
  cli.py        — `main()` argv switch
  __main__.py   — `python -m saturn` / zipapp entry
"""

from __future__ import annotations
