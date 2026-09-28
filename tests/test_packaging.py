"""対象外の環境では配布物をビルド・起動しない。"""

import os
import subprocess

import pytest

from scripts import build_binary
from tests.test_config import ROOT


@pytest.mark.parametrize(
    ("system", "machine"),
    [("Linux", "aarch64"), ("Linux", "arm64"), ("Darwin", "x86_64"), ("Windows", "AMD64")],
)
def test_build_rejects_unsupported_platform_before_building(monkeypatch, system, machine):
    monkeypatch.setattr(build_binary.platform, "system", lambda: system)
    monkeypatch.setattr(build_binary.platform, "machine", lambda: machine)

    def unexpected_build(*args, **kwargs):
        pytest.fail("対象外の環境でビルドを開始した")

    monkeypatch.setattr(build_binary.subprocess, "run", unexpected_build)
    with pytest.raises(SystemExit, match="Build on Linux x64 with Python 3.14"):
        build_binary.main()


@pytest.mark.parametrize(
    ("system", "machine"),
    [("Linux", "aarch64"), ("Linux", "arm64"), ("Darwin", "x86_64"), ("Linux", "i686")],
)
def test_launcher_rejects_unsupported_platform(tmp_path, system, machine):
    uname = tmp_path / "uname"
    uname.write_text(f'#!/bin/sh\ncase "$1" in\n-s) echo {system};;\n-m) echo {machine};;\nesac\n')
    uname.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "run-binary.sh"), "--help"],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert result.stderr.strip() == "PR Merge Readiness requires a Linux x64 runner."
    assert result.stdout == ""
