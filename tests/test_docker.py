"""`saturn docker` pass-through tests.

Stubs the `docker` binary on $PATH so the test runs without a real
docker install or engine.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import _test_setup  # noqa: F401


def _make_stub(tmp: Path, exit_code: int = 0) -> Path:
    """Write a fake `docker` shim that records argv + exits with `exit_code`."""
    stub_dir = tmp / "stub-bin"
    stub_dir.mkdir()
    log = tmp / "argv.log"
    docker = stub_dir / "docker"
    docker.write_text(
        f"""#!/usr/bin/env bash
echo "$@" >> {log}
exit {exit_code}
"""
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub_dir


class CmdDockerTests(unittest.TestCase):
    def test_no_args_prints_usage_exits_2(self) -> None:
        from saturn.docker import cmd_docker

        with self.assertRaises(SystemExit) as cm:
            cmd_docker([])
        self.assertEqual(cm.exception.code, 2)

    def test_forwards_argv_via_subprocess(self) -> None:
        # Spawn a child python that runs `from saturn.docker import cmd_docker;
        # cmd_docker([...])` with a stubbed `docker` on PATH. We can't easily
        # intercept subprocess.run from inside the same interpreter without
        # monkey-patching, but a child process is cheap and exercises the
        # real code path.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            stub_dir = _make_stub(tmp)
            log = tmp / "argv.log"

            env = os.environ.copy()
            env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"
            env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
            env["SATURN_SKIP_ENGINE_PROBE"] = "1"

            r = subprocess.run(
                [sys.executable, "-c",
                 "from saturn.docker import cmd_docker; cmd_docker(['ps', '-a'])"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 0)
            self.assertEqual(log.read_text().strip(), "ps -a")

    def test_propagates_returncode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            stub_dir = _make_stub(tmp, exit_code=42)

            env = os.environ.copy()
            env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"
            env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
            env["SATURN_SKIP_ENGINE_PROBE"] = "1"

            r = subprocess.run(
                [sys.executable, "-c",
                 "from saturn.docker import cmd_docker; cmd_docker(['version'])"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 42)


if __name__ == "__main__":
    unittest.main()
