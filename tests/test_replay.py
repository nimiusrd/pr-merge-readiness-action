"""保存済み facts と明示した Git commit だけで再評価できる。"""

import pytest
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from pr_merge_readiness.artifacts import provenance
from pr_merge_readiness.config import ACTION_REPOSITORY
from pr_merge_readiness.evaluate import assess
from pr_merge_readiness.replay import replay
from tests.test_config import ROOT, config_text
from tests.test_support import BASE, facts, policy


def test_explicit_commit_survives_poisoned_checkout_pythonpath_and_future_clock():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        package = root / "pr_merge_readiness"
        package.mkdir()
        for name in ("__init__.py", "contracts.py", "evaluate.py"):
            shutil.copyfile(ROOT / "pr_merge_readiness" / name, package / name)

        def git(*args):
            return subprocess.run(
                ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
            ).stdout.strip()

        git("init", "-q")
        git("add", "pr_merge_readiness")
        git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Trusted evaluator",
        )
        trusted = git("rev-parse", "HEAD")
        report = {
            **assess(facts(), policy()),
            "provenance": provenance("example/project", trusted, BASE, ".github/config.toml"),
        }
        (package / "evaluate.py").write_text('raise RuntimeError("untrusted checkout")\n')
        (root / "json.py").write_text('raise RuntimeError("consumer module")\n')
        env = {**os.environ, "PYTHONPATH": str(root), "TZ": "Pacific/Kiritimati"}
        report_path = root / "pr-1.json"
        report_path.write_text(json.dumps(report))
        command = [
            sys.executable,
            "-I",
            "-B",
            str(ROOT / "cli.py"),
            "replay",
            "--report",
            str(report_path),
            "--source-dir",
            str(root),
            "--source-repository",
            ACTION_REPOSITORY,
            "--action-sha",
            trusted,
        ]
        process = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
        assert process.returncode == 0, process.stdout + process.stderr
        assert json.loads(process.stdout)["matches"]
        assert replay(report, root, ACTION_REPOSITORY, trusted) == report
        for repository, commit in (("other/action", trusted), (ACTION_REPOSITORY, BASE)):
            with pytest.raises(ValueError):
                replay(report, root, repository, commit)
        for assessment in (report, report["label_assessment"]):
            assessment["decision"] = "WAITING"
            report_path.write_text(json.dumps(report))
            process = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
            assert process.returncode == 1, process.stdout + process.stderr
            assert not json.loads(process.stdout)["matches"]
            assessment["decision"] = "SHADOW_CONDITIONS_MET"


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
