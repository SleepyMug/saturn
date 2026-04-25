"""argv switch + help.

`main()` is the single entry point. Empty argv / `-h` / `help` prints
the help block; everything else is routed to a per-subcommand handler
or dropped into the compose pass-through.
"""

from __future__ import annotations

import argparse
import sys

from .base import cmd_base_default, cmd_base_build
from .docker import cmd_docker
from .engine import cmd_host_addr, passthrough
from .env import probe_engine
from .workspace import FLAGS, cmd_new


HELP = """\
saturn: compose-native dev-container wrapper with nested path translation.

A workspace is any directory with a `.saturn/` subdir containing
`compose.yaml` (+ usually `Dockerfile`). `saturn new [dir] [--flags]` seeds
that template. Every other argv is forwarded to `docker compose` after
the compose config is resolved (env substitution, path normalization)
and — inside a saturn container — bind-mount sources are translated from
container paths to their real host backing paths via `docker inspect` on
the current container.

Commands:
  new [dir]  [--ssh] [--gh] [--claude] [--codex] [--nesting]
                  create <dir>/.saturn/{Dockerfile,compose.yaml}.
                  flags append install blocks + bind mounts.
                  if none of --ssh/--gh/--claude/--codex is passed,
                  defaults to --ssh --gh --claude.
  base default       (re)build localhost/saturn-base:latest from inlined default
  base build <file>  (re)build the base from a user-supplied Dockerfile
  shell              alias for `saturn exec dev bash`
  host-addr          show the hostname to the host node address
  docker <args>      forward to the `docker` CLI with saturn's resolved
                     DOCKER_HOST (skips compose translation).
  <anything else>    forwarded to `docker compose -f .saturn/compose.json -p <ws>`

Compose override chain:
  Additional compose files merge onto `.saturn/compose.yaml` via docker
  compose's native `-f base -f override` semantics (later files layer on
  top — scalars replace, lists append, maps deep-merge). Two sources are
  picked up automatically:
    * `.saturn/compose.override*.yaml` in the workspace (sorted)
    * `SATURN_COMPOSE_OVERRIDES` env var (colon-separated absolute paths)
  Overrides participate in env substitution and nested bind-mount path
  translation just like the base file.

Hard deps: `docker` CLI with the `compose` plugin. Point DOCKER_HOST at
any engine socket (rootless podman's docker-compat socket works).
"""


def _print_help() -> None:
    print(HELP.rstrip())


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)

    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        _print_help()
        return

    # `saturn docker` is a shim around the docker CLI; the engine probe
    # would still help (sets DOCKER_BUILDKIT, fail-fast on cli/backend
    # mismatch). Run it for every non-help command so probe-skipping
    # doesn't have a per-command opt list.
    probe_engine()

    if argv[0] == "new":
        p = argparse.ArgumentParser(prog="saturn new")
        p.add_argument("target", nargs="?", default=None,
                       help="target directory (default: cwd)")
        for f in FLAGS:
            p.add_argument(f"--{f}", action="store_true",
                           help=f"include {f} bits in the template")
        cmd_new(p.parse_args(argv[1:]))
        return

    if argv[0] == "base":
        p = argparse.ArgumentParser(prog="saturn base")
        sub = p.add_subparsers(dest="sub")
        sub.add_parser("default").set_defaults(fn=cmd_base_default)
        pb = sub.add_parser("build")
        pb.add_argument("file", help="path to a Dockerfile (must COPY saturn /usr/local/bin/saturn)")
        pb.set_defaults(fn=cmd_base_build)
        args = p.parse_args(argv[1:])
        fn = getattr(args, "fn", None)
        if fn is None:
            p.print_help()
            return
        fn(args)
        return

    if argv[0] == "docker":
        cmd_docker(argv[1:])
        return

    if argv[0] == "shell":
        argv = ["exec", "dev", "bash"]

    if argv[0] == "host-addr":
        cmd_host_addr()
        return

    if argv[0] == "up" and not any(a in ("-d", "--detach", "-h", "--help") for a in argv[1:]):
        print(
            "note: 'saturn up' attaches to the dev container, whose command is "
            "'sleep infinity' — you'll see no output and Ctrl+C will stop it. "
            "Run 'saturn up -d' to start detached, then 'saturn shell' to enter.",
            file=sys.stderr,
        )

    passthrough(argv)
