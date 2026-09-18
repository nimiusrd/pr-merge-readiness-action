"""観測・判定の型と実行時検証。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict

Decision = Literal["SHADOW_CONDITIONS_MET", "HUMAN_REVIEW_REQUIRED", "INSUFFICIENT_DATA"]
ConditionStatus = Literal["pass", "blocked", "unknown"]


class Policy(TypedDict):
    stale_change_review_days: int


class Config(TypedDict):
    version: Literal[4]
    review: Policy


class PullRequest(TypedDict):
    number: int
    state: Literal["OPEN", "CLOSED", "MERGED"]
    head_sha: str
    base_sha: str
    base_ref: NotRequired[str]
    updated_at: NotRequired[str]
    additions: NotRequired[int]
    deletions: NotRequired[int]
    changed_files: NotRequired[int]


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


class ObservationChange(TypedDict):
    group: str
    identity: dict[str, Any] | None
    field: str
    before: Any
    after: Any


class DecisionMetadata(TypedDict):
    reviews: list[Review]


class Observations(TypedDict):
    schema_version: Literal[3]
    observed_at: str
    repository: str
    collection_errors: list[str]
    stable: bool
    pr: NotRequired[PullRequest]
    change: NotRequired[ChangeSize]
    files: NotRequired[list[ChangedFile]]
    change_history: NotRequired[ChangeHistory]
    reviews: NotRequired[list[Review]]
    rechecked: NotRequired[dict[str, bool]]
    observation_changes: NotRequired[list[ObservationChange] | None]


class Condition(TypedDict):
    name: str
    status: ConditionStatus
    # レビュー・件数・理由文など条件ごとに異なる。保存時にJSONへ変換する。
    detail: Any


class ConditionAssessment(TypedDict):
    decision: Decision
    conditions: list[Condition]


class Assessment(ConditionAssessment):
    observations: Observations
    policy: Policy


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
