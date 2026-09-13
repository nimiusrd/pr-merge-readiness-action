"""保存済み facts と明示した Git commit だけで再評価できる。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pr_merge_readiness.artifacts import provenance
from pr_merge_readiness.config import ACTION_REPOSITORY
from pr_merge_readiness.evaluate import assess
from pr_merge_readiness.replay import replay
from tests.test_config import ROOT
from tests.test_support import BASE, facts, policy


class ReplayTests(unittest.TestCase):
    def test_explicit_commit_survives_poisoned_checkout_pythonpath_and_future_clock(self):
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
                "provenance": provenance(
                    "example/project", trusted, BASE, ".github/config.toml", trusted
                ),
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
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertTrue(json.loads(process.stdout)["matches"])
            self.assertEqual(replay(report, root, ACTION_REPOSITORY, trusted), report)
            for repository, commit in (("other/action", trusted), (ACTION_REPOSITORY, BASE)):
                with self.assertRaises(ValueError):
                    replay(report, root, repository, commit)
            report["decision"] = "WAITING"
            report_path.write_text(json.dumps(report))
            process = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
            self.assertFalse(json.loads(process.stdout)["matches"])

    def test_cli_does_not_import_consumer_modules_or_sitecustomize(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("json", "tomllib", "sitecustomize", "pr_merge_readiness"):
                (root / f"{name}.py").write_text('raise RuntimeError("consumer code executed")\n')
            shutil.copyfile(ROOT / "examples/devops-tycoon.toml", root / "config.toml")
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(ROOT / "cli.py"),
                    "generate-workflow",
                    "--config",
                    "config.toml",
                    "--output",
                    "generated.yml",
                ],
                cwd=root,
                env={**os.environ, "PYTHONPATH": str(root)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("jobs:", (root / "generated.yml").read_text())
