"""End-to-end zipapp tests.

Exercises the build pipeline and verifies the produced single-file
binary handles --help, `docker` pass-through, and the workspace
discovery error path.
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


ROOT = Path(__file__).resolve().parent.parent
BUILD_PY = ROOT / "build.py"


class ZipappTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="saturn-zipapp-")
        cls.zipapp = Path(cls._tmp) / "saturn"
        r = subprocess.run(
            [sys.executable, str(BUILD_PY), "-o", str(cls.zipapp)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"build.py failed: {r.stdout}\n{r.stderr}")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _env(self, extra_path: str | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["SATURN_SKIP_ENGINE_PROBE"] = "1"
        if extra_path:
            env["PATH"] = f"{extra_path}:{env.get('PATH', '')}"
        return env

    def test_zipapp_built_and_executable(self) -> None:
        self.assertTrue(self.zipapp.is_file())
        self.assertTrue(os.access(self.zipapp, os.X_OK))

    def test_help_prints(self) -> None:
        r = subprocess.run([str(self.zipapp), "--help"],
                           env=self._env(), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("saturn:", r.stdout)
        self.assertIn("docker <args>", r.stdout)  # the new subcommand surfaces

    def test_docker_subcommand_forwards(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            stub_dir = tmp / "stub"
            stub_dir.mkdir()
            log = tmp / "argv.log"
            docker = stub_dir / "docker"
            docker.write_text(
                f"""#!/usr/bin/env bash
echo "$@" >> {log}
exit 0
"""
            )
            docker.chmod(docker.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            r = subprocess.run(
                [str(self.zipapp), "docker", "ps", "-a", "--format", "json"],
                env=self._env(extra_path=str(stub_dir)),
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(log.read_text().strip(), "ps -a --format json")

    def test_docker_subcommand_no_args_exits_2(self) -> None:
        r = subprocess.run([str(self.zipapp), "docker"],
                           env=self._env(), capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", r.stderr.lower())

    def test_passthrough_no_workspace_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run([str(self.zipapp), "ps"],
                               cwd=td, env=self._env(),
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("no .saturn/compose.yaml", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
