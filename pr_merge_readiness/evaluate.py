#!/usr/bin/env python3
"""変更履歴に基づく追加確認だけを判定する。マージ条件はGitHubに委ねる。"""

from __future__ import annotations

from typing import Any

from .contracts import (
    Assessment,
    Condition,
    ConditionAssessment,
    Decision,
    ConditionStatus,
    EvaluationError,
    Observations,
    Policy,
    boolean,
    file_history_path,
    integer,
    sha,
    string,
    timestamp,
)


def validate_policy(policy: Policy) -> Policy:
    if not isinstance(policy, dict) or set(policy) != {"stale_change_review_days"}:
        raise EvaluationError("invalid policy keys")
    days = integer(policy["stale_change_review_days"], "stale_change_review_days")
    if days == 0:
        raise EvaluationError("stale_change_review_days must be positive")
    return policy


def change_ages(facts: Observations, days: int) -> list[dict[str, Any]]:
    """保存時刻とbaseの履歴だけで計算する。現在時刻・API・作業ツリーに依存しない。"""
    observed_at = timestamp(facts["observed_at"], "observed_at")
    history = facts["change_history"]
    if sha(history["base_sha"]) != sha(facts["pr"]["base_sha"]):
        raise EvaluationError("change_history base SHA mismatch")
    files, records = facts["files"], history["files"]
    if not isinstance(files, list) or not isinstance(records, list):
        raise EvaluationError("change_history and files must be lists")
    expected = {string(file["path"], "file.path"): file_history_path(file) for file in files}
    indexed = {string(record["path"], "history.path"): record for record in records}
    if (
        len(expected) != len(files)
        or len(indexed) != len(records)
        or expected.keys() != indexed.keys()
        or len(files) != integer(facts["change"]["changed_files"], "changed_files")
    ):
        raise EvaluationError("change_history file set mismatch")
    ages = []
    for path, history_path in sorted(expected.items()):
        record = indexed[path]
        if record["history_path"] != history_path:
            raise EvaluationError("change_history path mismatch")
        if history_path is None:
            if record["last_commit_sha"] is not None or record["last_changed_at"] is not None:
                raise EvaluationError("new file must have null prior history")
            age_seconds = None
            status = "new"
        else:
            sha(record["last_commit_sha"])
            changed_at = timestamp(record["last_changed_at"], "last_changed_at")
            age_seconds = (observed_at - changed_at).total_seconds()
            if age_seconds < 0:
                raise EvaluationError("last_changed_at is after observed_at")
            status = "stale" if age_seconds > days * 86400 else "within_threshold"
        ages.append({**record, "age_seconds": age_seconds, "status": status})
    return ages


def conditions_decision(conditions: list[Condition]) -> Decision:
    states = {c["status"] for c in conditions}
    return (
        "INSUFFICIENT_DATA"
        if "unknown" in states
        else "HUMAN_REVIEW_REQUIRED"
        if "blocked" in states
        else "SHADOW_CONDITIONS_MET"
    )


def history_assessment(facts: Observations, policy: Policy) -> ConditionAssessment:
    """追加確認の対象と、その確認に必要な人間の承認だけを評価する。"""
    result: ConditionAssessment = {"decision": "INSUFFICIENT_DATA", "conditions": []}

    def condition(name: str, status: ConditionStatus, detail: Any) -> None:
        result["conditions"].append({"name": name, "status": status, "detail": detail})

    try:
        if type(facts["schema_version"]) is not int or facts["schema_version"] != 3:
            raise EvaluationError("unsupported facts schema")
        string(facts["observed_at"], "observed_at")
        if facts["collection_errors"]:
            raise EvaluationError("collection incomplete: " + ", ".join(facts["collection_errors"]))
        pr = facts["pr"]
        head = sha(pr["head_sha"])
        sha(pr["base_sha"])
        condition(
            "freshness",
            "pass" if boolean(facts["stable"], "stable") else "unknown",
            "History review inputs must agree in both samples",
        )
        days = policy["stale_change_review_days"]
        ages = change_ages(facts, days)
        stale = any(file["status"] == "stale" for file in ages)
        human_reviews = []
        if stale:
            # コメント・下書きは承認を取り消さない。変更要求は同じ人の承認を取り消す。
            latest = {}
            published = [r for r in facts["reviews"] if r["state"] != "PENDING"]
            for review in sorted(
                published,
                key=lambda r: (
                    string(r["submitted_at"], "review.submitted_at"),
                    integer(r["id"], "review.id"),
                ),
            ):
                state = review["state"]
                if state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED", "COMMENTED"}:
                    raise EvaluationError("unknown review state")
                if state != "COMMENTED":
                    latest[string(review["author"], "review.author")] = review
            for review in latest.values():
                if review["state"] != "APPROVED" or review["commit_sha"] != head:
                    continue
                actor = string(review["author_type"], "review.author_type")
                association = string(review["author_association"], "review.author_association")
                if actor == "User" and association in {"OWNER", "MEMBER", "COLLABORATOR"}:
                    human_reviews.append(review["id"])
        condition(
            "stale_change_review",
            "blocked" if stale and not human_reviews else "pass",
            {
                "threshold_days": days,
                "observed_at": facts["observed_at"],
                "base_sha": facts["pr"]["base_sha"],
                "files": ages,
                "required_human_approvals": int(stale),
                "human_approval_review_ids": sorted(human_reviews),
            },
        )
        for key in ("additions", "deletions", "changed_files"):
            integer(facts["change"][key], key)
        result["decision"] = conditions_decision(result["conditions"])
    except (KeyError, TypeError, ValueError) as error:
        condition("data_integrity", "unknown", str(error))
    return result


def assess(facts: Observations, policy: Policy) -> Assessment:
    validate_policy(policy)
    return {**history_assessment(facts, policy), "observations": facts, "policy": policy}
