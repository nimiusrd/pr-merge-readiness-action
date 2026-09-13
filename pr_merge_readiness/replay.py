"""明示した信頼済み Git ソースによる、ネットワーク不要の再評価。"""

import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .artifacts import validate_report
from .config import ACTION_REPOSITORY
from .contracts import EvaluationError, sha


def replay(report: dict, source_dir: Path, repository: str, action_sha: str) -> dict:
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
                (package / name).write_bytes(sources.extractfile(member).read())
        run = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                """
import json, socket, sys
def denied(*args, **kwargs):
    raise RuntimeError("network forbidden during replay")
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
socket.getaddrinfo = denied
sys.path.insert(0, sys.argv[1])
from pr_merge_readiness.evaluate import assess
report = json.load(sys.stdin)
result = assess(report["observations"], report["policy"])
result["provenance"] = report["provenance"]
print(json.dumps(result))
""",
                str(root),
            ],
            cwd=root,
            input=json.dumps(report),
            check=True,
            capture_output=True,
            text=True,
        )
    return json.loads(run.stdout)
