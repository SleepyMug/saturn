"""Workspace template seeding (`saturn new`) + cwd-upward discovery.

The `.saturn/` directory containing `compose.yaml` is the workspace
identity. `cmd_new` writes the template pair from CLI flags;
`find_workspace` walks cwd upward to associate a pass-through
invocation with one.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .env import BASE_IMAGE, IS_HOST


# ---------- templates for `saturn new` ----------------------------------

_DF_HEAD = "FROM {base}\n"

_DF_INSTALL = {
    "ssh": (
        "RUN apt-get update \\\n"
        " && apt-get install -y --no-install-recommends openssh-client \\\n"
        " && rm -rf /var/lib/apt/lists/*\n"
    ),
    "gh": (
        "RUN apt-get update \\\n"
        " && apt-get install -y --no-install-recommends gh \\\n"
        " && rm -rf /var/lib/apt/lists/*\n"
    ),
    "claude": (
        "RUN curl -fsSL https://claude.ai/install.sh | bash\n"
        "ENV PATH=/root/.local/bin:${PATH}\n"
    ),
    # codex needs nodejs+npm.
    "codex": (
        "RUN apt-get update \\\n"
        " && apt-get install -y --no-install-recommends nodejs npm \\\n"
        " && rm -rf /var/lib/apt/lists/* \\\n"
        " && npm install -g @openai/codex\n"
    ),
}

_COMPOSE_HEAD = """\
services:
  dev:
    build:
      context: ..
      dockerfile: .saturn/Dockerfile
    image: localhost/saturn-{name}:latest
    container_name: saturn_{name}
    init: true
    working_dir: /root/{name}
    command: ["sleep", "infinity"]
    environment:
      SATURN_IN_GUEST: "1"
      SATURN_SOCK: /var/run/docker.sock
"""

_COMPOSE_EXTRA_HOSTS = (
    '    extra_hosts:\n'
    '      - "host.docker.internal:host-gateway"\n'
)

_COMPOSE_VOLUMES_HEAD = """\
    volumes:
      - ..:/root/{name}
"""

_COMPOSE_VOLUMES = {
    "nesting": "      - ${SATURN_SOCK}:/var/run/docker.sock\n",
    "ssh":    "      - ${HOME}/.ssh:/root/.ssh\n",
    "gh":     "      - ${HOME}/.config/gh:/root/.config/gh\n",
    "claude": (
        "      - ${HOME}/.claude:/root/.claude\n"
        "      - ${HOME}/.claude.json:/root/.claude.json\n"
    ),
    "codex":  "      - ${HOME}/.codex:/root/.codex\n",
}

# Host-mode auto-create: make sure the host-side bind source exists
# before docker tries to mount it. (Docker auto-creates missing dirs,
# but file bind mounts fail if the source doesn't exist.)
_AUTOCREATE = {
    "ssh":    [(".ssh", "dir")],
    "gh":     [(".config/gh", "dir")],
    "claude": [(".claude", "dir"), (".claude.json", "file")],
    "codex":  [(".codex", "dir")],
}

FLAGS = ("ssh", "gh", "claude", "codex", "nesting")


# ---------- name normalization ------------------------------------------

# Compose project names (and image refs) share a tight regex. Compose is
# the strictest of the three: `^[a-z0-9][a-z0-9_-]*$`. Image refs allow
# `.` as a separator; containers allow uppercase. We normalize to the
# compose regex so the same string is valid as all three.
_VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def normalize_name(raw: str) -> str:
    """Lowercase + replace invalid chars with '-'; collapse runs; trim ends.

    Exits if the result is empty or doesn't start with [a-z0-9]. Printed
    warning when normalization actually changed anything — the disk dir
    keeps its original name; only the embedded image/container/project
    identity uses the normalized form.
    """
    s = raw.lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-_")
    if not s:
        sys.exit(f"basename {raw!r} normalizes to an empty string — rename the directory")
    if not _VALID_NAME.match(s):
        sys.exit(f"basename {raw!r} normalizes to {s!r}, which still isn't a valid compose project name")
    if s != raw:
        print(f"note: basename {raw!r} normalized to {s!r} for image/container/project name")
    return s


# ---------- workspace discovery -----------------------------------------

def find_workspace() -> Path:
    """Walk up from cwd to find a dir containing .saturn/compose.yaml."""
    cur = Path.cwd().resolve()
    while True:
        if (cur / ".saturn" / "compose.yaml").is_file():
            return cur
        if cur.parent == cur:
            sys.exit("no .saturn/compose.yaml in cwd or any parent  (try: saturn new)")
        cur = cur.parent


# ---------- cmd: new ----------------------------------------------------

def cmd_new(args: argparse.Namespace) -> None:
    target = Path(args.target if args.target else ".").resolve()
    target.mkdir(parents=True, exist_ok=True)
    if not target.name or target.name.startswith("."):
        sys.exit(f"invalid workspace dir: {target}  (must have a non-empty basename not starting with '.')")
    name = normalize_name(target.name)

    # If the user didn't pick any of the mixin-style flags, seed the
    # common defaults (ssh + gh + claude). --nesting is orthogonal and
    # does not count as "specifying a mixin" for this check.
    if not (args.ssh or args.gh or args.claude or args.codex):
        args.ssh = args.gh = args.claude = True

    saturn_dir = target / ".saturn"
    saturn_dir.mkdir(parents=True, exist_ok=True)

    # Dockerfile
    df = saturn_dir / "Dockerfile"
    if not df.exists():
        parts = [_DF_HEAD.format(base=BASE_IMAGE), "\n"]
        if args.ssh:
            parts.append(_DF_INSTALL["ssh"])
        if args.gh:
            parts.append(_DF_INSTALL["gh"])
        if args.claude:
            parts.append(_DF_INSTALL["claude"])
        if args.codex:
            parts.append(_DF_INSTALL["codex"])
        df.write_text("".join(parts))
        print(f"seeded:  {df}")

    # compose.yaml
    cf = saturn_dir / "compose.yaml"
    if not cf.exists():
        buf = _COMPOSE_HEAD.format(name=name)
        if args.nesting:
            buf += _COMPOSE_EXTRA_HOSTS
        buf += _COMPOSE_VOLUMES_HEAD.format(name=name)
        if args.nesting:
            buf += _COMPOSE_VOLUMES["nesting"]
        if args.ssh:
            buf += _COMPOSE_VOLUMES["ssh"]
        if args.gh:
            buf += _COMPOSE_VOLUMES["gh"]
        if args.claude:
            buf += _COMPOSE_VOLUMES["claude"]
        if args.codex:
            buf += _COMPOSE_VOLUMES["codex"]
        cf.write_text(buf)
        print(f"seeded:  {cf}")

    # Host-mode: materialize missing bind sources under $HOME.
    if IS_HOST:
        home = Path.home()
        for flag in ("ssh", "gh", "claude", "codex"):
            if not getattr(args, flag):
                continue
            for rel, kind in _AUTOCREATE[flag]:
                p = home / rel
                if p.exists():
                    continue
                if kind == "dir":
                    p.mkdir(parents=True, exist_ok=True)
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.touch()
                print(f"created {kind}: {p}  ({flag})")

    print(f"workspace: {target}")
    print(f"  next: $EDITOR {df} && saturn up")
