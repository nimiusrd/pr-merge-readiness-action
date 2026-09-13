"""利用側 TOML を厳密に検証し、評価用 policy に正規化する。"""

import tomllib
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any, cast

from .contracts import Config, EvaluationError, Policy, boolean, integer, sha, string
from .evaluate import validate_policy

ACTION_REPOSITORY = "nimiusrd/pr-merge-readiness-action"
CONFIG_PATH = ".github/pr-merge-readiness.toml"


def keys(value: object, required: Iterable[str], optional: Iterable[str] = ()) -> None:
    if not isinstance(value, dict):
        raise EvaluationError("table required")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    if missing or unknown:
        raise EvaluationError(f"invalid keys: missing={sorted(missing)}, unknown={sorted(unknown)}")


def relative_path(value: str) -> str:
    string(value, "path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or str(path) != value
        or "\\" in value
        or any(ord(c) < 32 for c in value)
        or value == "."
    ):
        raise EvaluationError("normalized repository-relative path required")
    return value


def validate_config(value: dict[str, Any]) -> Config:
    keys(value, {"version", "action_ref", "ci", "review"}, {"publication"})
    if type(value["version"]) is not int or value["version"] != 1:
        raise EvaluationError("unsupported config version")
    sha(value["action_ref"])
    keys(value["ci"], {"workflows", "required_checks"})
    workflows = value["ci"]["workflows"]
    if not isinstance(workflows, list) or not workflows:
        raise EvaluationError("nonempty ci.workflows required")
    for name in workflows:
        string(name, "workflow name")
        if name.strip() != name or any(ord(c) < 32 for c in name):
            raise EvaluationError("invalid workflow name")
    if len(set(workflows)) != len(workflows):
        raise EvaluationError("duplicate workflow")
    keys(
        value["review"],
        {"minimum_approvals", "require_resolved_threads", "stale_change_review_days"},
    )
    checks = value["ci"]["required_checks"]
    if not isinstance(checks, list):
        raise EvaluationError("required_checks must be an array")
    for check in checks:
        if not isinstance(check, dict) or check.get("kind") not in {"check_run", "status"}:
            raise EvaluationError("unknown check kind")
        keys(check, {"kind", "name", "app_id" if check["kind"] == "check_run" else "creator"})
    validate_policy(cast(Policy, {**value["review"], "required_checks": checks}))
    publication = value.get("publication", {})
    keys(publication, set(), {"checks", "labels"})
    publication = {"checks": True, "labels": "manual", **publication}
    boolean(publication["checks"], "publication.checks")
    if publication["labels"] not in ("manual", "off"):
        raise EvaluationError("publication.labels must be manual or off")
    return cast(Config, {**value, "publication": publication})


def load_config(path: Path) -> Config:
    with path.open("rb") as stream:
        return validate_config(tomllib.load(stream))


def policy_from(config: Config) -> Policy:
    return {
        "minimum_approvals": config["review"]["minimum_approvals"],
        "require_resolved_threads": config["review"]["require_resolved_threads"],
        "stale_change_review_days": config["review"]["stale_change_review_days"],
        "required_checks": config["ci"]["required_checks"],
    }


def positive(value: str) -> int:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
    ):
        raise EvaluationError("positive decimal integer required")
    result = integer(int(value), "number")
    if result == 0:
        raise EvaluationError("positive integer required")
    return result
