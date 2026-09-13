"""新しい観測・判定の型と実行時検証。旧 artifact は受理しない。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict

Decision = Literal["SHADOW_CONDITIONS_MET", "WAITING", "HUMAN_REVIEW_REQUIRED", "INSUFFICIENT_DATA"]
ConditionStatus = Literal["pass", "waiting", "blocked", "unknown"]


class RequiredCheckRun(TypedDict):
    kind: Literal["check_run"]
    name: str
    app_id: int


class RequiredStatus(TypedDict):
    kind: Literal["status"]
    name: str
    creator: str


RequiredCheck = RequiredCheckRun | RequiredStatus


class ReviewConfig(TypedDict):
    minimum_approvals: int
    require_resolved_threads: bool
    stale_change_review_days: int


class Policy(ReviewConfig):
    required_checks: list[RequiredCheck]


class CIConfig(TypedDict):
    workflows: list[str]
    required_checks: list[RequiredCheck]


class PublicationConfig(TypedDict):
    checks: bool
    labels: Literal["manual", "off"]


class Config(TypedDict):
    version: Literal[1]
    action_ref: str
    ci: CIConfig
    review: ReviewConfig
    publication: PublicationConfig


class Source(TypedDict):
    repository: str
    sha: str
    path: str


class Provenance(TypedDict):
    evaluator: Source
    config: Source
    workflow: Source | None


class PullRequest(TypedDict):
    number: int
    state: Literal["OPEN", "CLOSED", "MERGED"]
    draft: bool
    head_sha: str
    base_sha: str
    merge_sha: str | None
    mergeable: Literal["MERGEABLE", "CONFLICTING", "UNKNOWN"]
    merge_state: str
    review_decision: str | None
    base_ref: NotRequired[str]
    updated_at: NotRequired[str]
    additions: NotRequired[int]
    deletions: NotRequired[int]
    changed_files: NotRequired[int]


class CheckRun(RequiredCheckRun):
    id: int
    sha: str
    status: str
    conclusion: str | None


class CommitStatus(RequiredStatus):
    id: int
    sha: str
    status: str
    conclusion: str | None


Check = CheckRun | CommitStatus


class Review(TypedDict):
    id: int
    author: str | None
    state: str
    commit_sha: str | None
    submitted_at: str | None
    author_type: NotRequired[str | None]
    author_association: NotRequired[str | None]


class ChangeSize(TypedDict):
    additions: int
    deletions: int
    changed_files: int
    types: NotRequired[dict[str, int]]
    binary_files: NotRequired[int | None]
    mode_changes: NotRequired[int | None]


class ChangedFile(TypedDict):
    path: str
    previous_path: NotRequired[str | None]
    changeType: str
    additions: int
    deletions: int


class FileHistory(TypedDict):
    path: str
    history_path: str | None
    last_commit_sha: str | None
    last_changed_at: str | None


class ChangeHistory(TypedDict):
    base_sha: str
    files: list[FileHistory]


class CIHistory(TypedDict):
    sha: str
    kind: str
    name: str
    producer: int | str
    records: int
    failure_before_success: bool


class ObservationChange(TypedDict):
    group: str
    identity: dict[str, Any] | None
    field: str
    before: Any
    after: Any


class DecisionMetadata(TypedDict):
    reviews: list[Review]
    unresolved_threads: int
    checks: list[Check]


class Observations(TypedDict):
    schema_version: Literal[1]
    observed_at: str
    repository: str
    collection_errors: list[str]
    stable: bool
    pr: NotRequired[PullRequest]
    change: NotRequired[ChangeSize]
    files: NotRequired[list[ChangedFile]]
    change_history: NotRequired[ChangeHistory]
    reviews: NotRequired[list[Review]]
    unresolved_threads: NotRequired[int]
    checks: NotRequired[list[Check]]
    ci_definition_changes: NotRequired[list[str]]
    ci_history: NotRequired[list[CIHistory]]
    rechecked: NotRequired[dict[str, bool]]
    observation_changes: NotRequired[list[ObservationChange] | None]


class Condition(TypedDict):
    name: str
    status: ConditionStatus
    # CI記録・件数・理由文など条件ごとに異なる。保存時にJSONへ変換する。
    detail: Any


class Assessment(TypedDict):
    format: Literal["pr-merge-readiness/report"]
    # 純粋な評価の後、保存前に信頼済みソース情報を付与する。
    provenance: NotRequired[Provenance]
    schema_version: Literal[1]
    mode: Literal["shadow"]
    decision: Decision
    conditions: list[Condition]
    observations: Observations
    policy: Policy
    policy_sha256: str


class Manifest(TypedDict):
    format: Literal["pr-merge-readiness/manifest"]
    schema_version: Literal[1]
    provenance: Provenance
    artifact_name: str
    repository: str
    run_id: str
    run_attempt: str
    started_at: str
    reports: list[str]
    collection_failed: bool
    selection: Literal["single_pr", "all_open"]


class EvaluationError(ValueError):
    """欠落・不正なデータを成功として扱わない。"""


def integer(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise EvaluationError(f"{name}: non-negative integer required")
    return value


def string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvaluationError(f"{name}: non-empty string required")
    return value


def boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise EvaluationError(f"{name}: boolean required")
    return value


def sha(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise EvaluationError("invalid commit SHA")
    return value


def timestamp(value: Any, name: str) -> datetime:
    parsed = datetime.fromisoformat(string(value, name))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvaluationError(f"{name}: timezone required")
    return parsed


def file_history_path(file: ChangedFile) -> str | None:
    """追加・コピーは新規。renameではbaseに存在する旧pathを調べる。"""
    path = string(file["path"], "file.path")
    change = file["changeType"]
    if change in {"ADDED", "COPIED"}:
        return None
    if change == "RENAMED":
        return string(file["previous_path"], "file.previous_path")
    if change in {"MODIFIED", "DELETED", "CHANGED", "UNCHANGED"}:
        return path
    raise EvaluationError("unknown file changeType")
