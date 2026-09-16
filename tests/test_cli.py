"""CLIは利用側のモジュール・Python環境を実行しない。"""

import json
import os
import subprocess
import sys

import pytest

from tests.test_config import ROOT, config_text


@pytest.mark.parametrize("launcher", ["python", "uv"])
def test_cli_does_not_import_consumer_modules_or_project_environment(tmp_path, launcher):
    for name in ("json", "tomllib", "sitecustomize", "pr_merge_readiness"):
        (tmp_path / f"{name}.py").write_text('raise RuntimeError("consumer code executed")\n')
    (tmp_path / "pyproject.toml").write_text("not valid TOML = [")
    (tmp_path / "uv.toml").write_text("not valid TOML = [")
    (tmp_path / ".python-version").write_text("9.99\n")
    virtualenv = tmp_path / ".venv"
    (virtualenv / "bin").mkdir(parents=True)
    poison = virtualenv / "bin/python"
    poison.write_text('#!/bin/sh\ntouch "' + str(tmp_path / "executed") + '"\nexit 99\n')
    poison.chmod(0o755)
    (tmp_path / "config.toml").write_text(config_text())
    command = (
        [sys.executable, "-I", "-B", str(ROOT / "cli.py")]
        if launcher == "python"
        else ["bash", str(ROOT / "run.sh")]
    )
    result = subprocess.run(
        [*command, "validate-config", "--config", "config.toml"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(tmp_path),
            "VIRTUAL_ENV": str(virtualenv),
            "UV_PROJECT": str(tmp_path),
            "UV_CONFIG_FILE": str(tmp_path / "uv.toml"),
            "UV_PYTHON": str(poison),
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        },
        capture_output=True,
        text=True,
    )
    assert not (tmp_path / "executed").exists()
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"valid": True}
