"""明示した信頼済み Git ソースによる、ネットワーク不要の再評価。"""

import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, cast

from .artifacts import validate_report
from .config import ACTION_REPOSITORY
from .contracts import Assessment, EvaluationError, sha
from .process import system_environment


def replay(
    report: dict[str, Any], source_dir: Path, repository: str, action_sha: str
) -> Assessment:
    validate_report(report)
    expected = {"repository": repository, "sha": sha(action_sha), "path": "pr_merge_readiness"}
    if repository != ACTION_REPOSITORY or report["provenance"]["evaluator"] != expected:
        raise EvaluationError("report does not match the explicitly trusted evaluator")
    archive = subprocess.run(
        [
            "git",
            "-C",
            str(source_dir),
            "archive",
            "--format=tar",
            action_sha,
            "pr_merge_readiness/",
        ],
        check=True,
        capture_output=True,
        env=system_environment(),
    ).stdout
    with tempfile.TemporaryDirectory(prefix="readiness-replay-") as directory:
        root = Path(directory)
        package = root / "pr_merge_readiness"
        package.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as sources:
            for name in ("__init__.py", "evaluate.py", "contracts.py"):
                member = sources.getmember("pr_merge_readiness/" + name)
                if not member.isfile():
                    raise EvaluationError("evaluator modules must be regular files")
                stream = sources.extractfile(member)
                if stream is None:
                    raise EvaluationError("evaluator module content is missing")
                (package / name).write_bytes(stream.read())
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command += ["-I", "-B", str(Path(__file__).resolve().parent.parent / "cli.py")]
        run = subprocess.run(
            [*command, "_replay-worker", str(root)],
            cwd=root,
            input=json.dumps(report),
            check=True,
            capture_output=True,
            text=True,
        )
    return cast(Assessment, json.loads(run.stdout))
