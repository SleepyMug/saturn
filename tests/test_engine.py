"""Engine helpers: `_translate`, `_find_overrides`, override env var."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from . import _test_setup  # noqa: F401

from saturn.engine import _find_overrides, _translate


class TranslateTests(unittest.TestCase):
    """`_translate(source, mounts)` reverse-lookups a host path."""

    MOUNTS = [
        {"Type": "bind", "Source": "/host/code/myproj", "Destination": "/root/myproj"},
        {"Type": "bind", "Source": "/host/run/docker.sock", "Destination": "/var/run/docker.sock"},
        {"Type": "bind", "Source": "/host/home/user/.ssh", "Destination": "/root/.ssh"},
        {"Type": "volume", "Source": "noisy", "Destination": "/cache"},
    ]

    def test_exact_destination_returns_host_source(self) -> None:
        self.assertEqual(
            _translate("/var/run/docker.sock", self.MOUNTS),
            "/host/run/docker.sock",
        )

    def test_subpath_appended(self) -> None:
        self.assertEqual(
            _translate("/root/myproj/sub/dir", self.MOUNTS),
            "/host/code/myproj/sub/dir",
        )

    def test_unrelated_path_returns_none(self) -> None:
        self.assertIsNone(_translate("/etc/hostname", self.MOUNTS))

    def test_skips_non_bind_mounts(self) -> None:
        # Even though /cache is in the mount list, it's a volume, not a bind.
        self.assertIsNone(_translate("/cache/x", self.MOUNTS))

    def test_longest_match_wins(self) -> None:
        mounts = [
            {"Type": "bind", "Source": "/host/outer", "Destination": "/root"},
            {"Type": "bind", "Source": "/host/inner", "Destination": "/root/proj"},
        ]
        # /root/proj/foo should match the longer destination.
        self.assertEqual(_translate("/root/proj/foo", mounts), "/host/inner/foo")
        # /root/foo only matches the short one.
        self.assertEqual(_translate("/root/foo", mounts), "/host/outer/foo")


class FindOverridesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_env = os.environ.pop("SATURN_COMPOSE_OVERRIDES", None)

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("SATURN_COMPOSE_OVERRIDES", None)
        else:
            os.environ["SATURN_COMPOSE_OVERRIDES"] = self._old_env

    def test_no_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / ".saturn").mkdir()
            (ws / ".saturn" / "compose.yaml").write_text("services: {}\n")
            self.assertEqual(_find_overrides(ws), [])

    def test_workspace_glob_sorted_lexically(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / ".saturn").mkdir()
            (ws / ".saturn" / "compose.yaml").write_text("x")
            (ws / ".saturn" / "compose.override.b.yaml").write_text("x")
            (ws / ".saturn" / "compose.override.a.yaml").write_text("x")
            (ws / ".saturn" / "compose.override.yaml").write_text("x")
            got = _find_overrides(ws)
            self.assertEqual(
                [p.name for p in got],
                ["compose.override.a.yaml", "compose.override.b.yaml", "compose.override.yaml"],
            )

    def test_env_var_appends(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / ".saturn").mkdir()
            (ws / ".saturn" / "compose.yaml").write_text("x")
            extra1 = Path(td) / "extra1.yaml"
            extra1.write_text("x")
            extra2 = Path(td) / "extra2.yaml"
            extra2.write_text("x")
            os.environ["SATURN_COMPOSE_OVERRIDES"] = f"{extra1}:{extra2}"
            got = _find_overrides(ws)
            self.assertEqual([p.resolve() for p in got], [extra1.resolve(), extra2.resolve()])

    def test_env_var_skips_empty_segments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / ".saturn").mkdir()
            (ws / ".saturn" / "compose.yaml").write_text("x")
            extra = Path(td) / "extra.yaml"
            extra.write_text("x")
            os.environ["SATURN_COMPOSE_OVERRIDES"] = f"::{extra}::"
            got = _find_overrides(ws)
            self.assertEqual([p.resolve() for p in got], [extra.resolve()])


if __name__ == "__main__":
    unittest.main()
