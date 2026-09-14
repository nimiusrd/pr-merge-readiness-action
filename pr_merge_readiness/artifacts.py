"""同じ実行・設定・評価器による artifact だけを公開する。"""

import hashlib
import json
from pathlib import Path
from typing import Any, Final, cast, get_args

from .config import ACTION_REPOSITORY, positive, relative_path
from .contracts import Assessment, Decision, EvaluationError, Policy, Provenance, sha
from .evaluate import validate_policy

REPORT_FORMAT: Final = "pr-merge-readiness/report"
MANIFEST_FORMAT: Final = "pr-merge-readiness/manifest"


def fingerprint(policy: Policy) -> str:
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


def provenance(repository: str, action_sha: str, config_sha: str, config_path: str) -> Provenance:
    return {
        "evaluator": {
            "repository": ACTION_REPOSITORY,
            "sha": sha(action_sha),
            "path": "pr_merge_readiness",
        },
        "config": {
            "repository": repository,
            "sha": sha(config_sha),
            "path": relative_path(config_path),
        },
        "workflow": None,
    }


def artifact_name(run_id: str, attempt: str) -> str:
    positive(run_id)
    positive(attempt)
    return f"pr-merge-readiness-{run_id}-{attempt}"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def validate_report(report: dict[str, Any]) -> Assessment:
    if (
        report.get("format") != REPORT_FORMAT
        or type(report.get("schema_version")) is not int
        or report["schema_version"] != 2
    ):
        raise EvaluationError("unsupported report format")
    labels = report.get("label_assessment")
    if (
        not isinstance(labels, dict)
        or labels.get("decision") not in get_args(Decision)
        or not isinstance(labels.get("conditions"), list)
    ):
        raise EvaluationError("invalid label assessment")
    validate_policy(report["policy"])
    if fingerprint(report["policy"]) != report["policy_sha256"]:
        raise EvaluationError("policy fingerprint mismatch")
    source = report["provenance"]
    expected = provenance(
        report["observations"]["repository"],
        source["evaluator"]["sha"],
        source["config"]["sha"],
        source["config"]["path"],
    )
    if source != expected:
        raise EvaluationError("invalid provenance")
    return cast(Assessment, report)


def load_reports(
    directory: Path,
    repository: str,
    run_id: str,
    attempt: str,
    name: str,
    source: Provenance,
    policy: Policy,
) -> list[tuple[int, Assessment]]:
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest.get("format") != MANIFEST_FORMAT
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
    ):
        raise EvaluationError("unsupported manifest format")
    if (
        manifest["repository"] != repository
        or manifest["run_id"] != run_id
        or manifest["run_attempt"] != attempt
        or manifest["artifact_name"] != name
        or name != artifact_name(run_id, attempt)
        or manifest["provenance"] != source
    ):
        raise EvaluationError("manifest repository/run/attempt/artifact/provenance mismatch")
    if manifest["collection_failed"] is not False:
        raise EvaluationError("collection failed; no complete manifest")
    if manifest["selection"] not in {"single_pr", "all_open"}:
        raise EvaluationError("unknown observation selection")
    files = manifest["reports"]
    if (
        not isinstance(files, list)
        or any(not isinstance(f, str) for f in files)
        or len(files) != len(set(files))
    ):
        raise EvaluationError("invalid manifest reports")
    reports = []
    for filename in files:
        if not filename.startswith("pr-") or not filename.endswith(".json"):
            raise EvaluationError("invalid report filename")
        number = positive(filename[3:-5])
        path = directory / filename
        if path.is_symlink():
            raise EvaluationError("report must be a regular file")
        report = validate_report(json.loads(path.read_text()))
        if (
            report["provenance"] != source
            or report["policy"] != policy
            or report["observations"]["repository"] != repository
            or report["observations"].get("pr", {}).get("number", number) != number
        ):
            raise EvaluationError("report identity/policy mismatch")
        reports.append((number, report))
    # 全ファイルを検証してから書込みを開始する。
    return reports
