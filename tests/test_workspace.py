"""Workspace seeding + discovery + name normalization tests."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path

from . import _test_setup  # noqa: F401  — sets sys.path

from saturn.workspace import (
    FLAGS,
    cmd_new,
    find_workspace,
    normalize_name,
)


def _new_args(target: str | None = None, **flags: bool) -> argparse.Namespace:
    """Build the Namespace shape `cmd_new` expects (mirrors cli.py)."""
    ns = argparse.Namespace(target=target)
    for f in FLAGS:
        setattr(ns, f, bool(flags.get(f, False)))
    return ns


class NormalizeNameTests(unittest.TestCase):
    def test_lowercases(self) -> None:
        self.assertEqual(normalize_name("MyProj"), "myproj")

    def test_replaces_invalid_chars(self) -> None:
        self.assertEqual(normalize_name("weird.name @2"), "weird-name-2")

    def test_collapses_runs_and_trims(self) -> None:
        self.assertEqual(normalize_name("--foo--bar--"), "foo-bar")

    def test_passes_through_valid(self) -> None:
        self.assertEqual(normalize_name("a-b_c-2"), "a-b_c-2")

    def test_empty_after_normalize_exits(self) -> None:
        with self.assertRaises(SystemExit):
            normalize_name("...")


class FindWorkspaceTests(unittest.TestCase):
    def test_finds_workspace_at_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "proj"
            (ws / ".saturn").mkdir(parents=True)
            (ws / ".saturn" / "compose.yaml").write_text("services: {}\n")
            cwd = os.getcwd()
            try:
                os.chdir(ws)
                self.assertEqual(find_workspace(), ws.resolve())
            finally:
                os.chdir(cwd)

    def test_walks_up(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "proj"
            sub = ws / "a" / "b" / "c"
            sub.mkdir(parents=True)
            (ws / ".saturn").mkdir()
            (ws / ".saturn" / "compose.yaml").write_text("services: {}\n")
            cwd = os.getcwd()
            try:
                os.chdir(sub)
                self.assertEqual(find_workspace(), ws.resolve())
            finally:
                os.chdir(cwd)

    def test_missing_exits(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            try:
                os.chdir(td)
                with self.assertRaises(SystemExit):
                    find_workspace()
            finally:
                os.chdir(cwd)


class CmdNewTests(unittest.TestCase):
    def setUp(self) -> None:
        # Re-route $HOME so cmd_new's host-mode auto-create doesn't touch
        # the real homedir.
        self._home = os.environ.get("HOME")
        self._tmp_home = tempfile.mkdtemp(prefix="saturn-test-home-")
        os.environ["HOME"] = self._tmp_home

    def tearDown(self) -> None:
        if self._home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home
        import shutil
        shutil.rmtree(self._tmp_home, ignore_errors=True)

    def test_seeds_dockerfile_and_compose(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "myproj"
            cmd_new(_new_args(target=str(target), ssh=True))
            df = target / ".saturn" / "Dockerfile"
            cf = target / ".saturn" / "compose.yaml"
            self.assertTrue(df.is_file())
            self.assertTrue(cf.is_file())
            self.assertIn("openssh-client", df.read_text())
            self.assertIn("/root/.ssh", cf.read_text())
            self.assertIn("container_name: saturn_myproj", cf.read_text())

    def test_default_flags_when_none_specified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "myproj"
            cmd_new(_new_args(target=str(target)))
            cf = target / ".saturn" / "compose.yaml"
            text = cf.read_text()
            # ssh, gh, claude all default-on; codex stays off.
            self.assertIn("/root/.ssh", text)
            self.assertIn("/root/.config/gh", text)
            self.assertIn("/root/.claude", text)
            self.assertNotIn("/root/.codex", text)

    def test_nesting_adds_socket_and_extra_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "myproj"
            cmd_new(_new_args(target=str(target), nesting=True, ssh=True))
            text = (target / ".saturn" / "compose.yaml").read_text()
            self.assertIn("host.docker.internal:host-gateway", text)
            self.assertIn("${SATURN_SOCK}:/var/run/docker.sock", text)

    def test_nesting_alone_still_gets_default_mixins(self) -> None:
        # `--nesting` is orthogonal to ssh/gh/claude/codex defaults.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "myproj"
            cmd_new(_new_args(target=str(target), nesting=True))
            text = (target / ".saturn" / "compose.yaml").read_text()
            self.assertIn("/root/.ssh", text)
            self.assertIn("/root/.config/gh", text)
            self.assertIn("/root/.claude", text)

    def test_idempotent_existing_files_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "myproj"
            (target / ".saturn").mkdir(parents=True)
            df = target / ".saturn" / "Dockerfile"
            cf = target / ".saturn" / "compose.yaml"
            df.write_text("CUSTOM\n")
            cf.write_text("CUSTOM\n")
            cmd_new(_new_args(target=str(target), ssh=True))
            self.assertEqual(df.read_text(), "CUSTOM\n")
            self.assertEqual(cf.read_text(), "CUSTOM\n")


if __name__ == "__main__":
    unittest.main()
