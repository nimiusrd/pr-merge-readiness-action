#!/usr/bin/env python3
"""正規化したGit/PR/CIの観測事実に、明示的なShadow条件を適用する。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import (
    Assessment,
    ConditionStatus,
    EvaluationError,
    Observations,
    Policy,
    RequiredCheck,
    boolean,
    file_history_path,
    integer,
    sha,
    string,
    timestamp,
)


def validate_policy(policy: Policy) -> Policy:
    if not isinstance(policy, dict) or set(policy) != {
        "minimum_approvals",
        "require_resolved_threads",
        "stale_change_review_days",
        "required_checks",
    }:
        raise EvaluationError("invalid policy keys")
    integer(policy["minimum_approvals"], "minimum_approvals")
    boolean(policy["require_resolved_threads"], "require_resolved_threads")
    days = integer(policy["stale_change_review_days"], "stale_change_review_days")
    if days == 0:
        raise EvaluationError("stale_change_review_days must be positive")
    checks = policy["required_checks"]
    if not isinstance(checks, list) or not checks:
        raise EvaluationError("required_checks must explicitly contain at least one check")
    identities = set()
    for check in checks:
        if not isinstance(check, dict) or check.get("kind") not in {"check_run", "status"}:
            raise EvaluationError("unknown check kind")
        if set(check) != {"kind", "name", "app_id" if check["kind"] == "check_run" else "creator"}:
            raise EvaluationError("invalid required check keys")
        name = string(check["name"], "check.name")
        kind = check["kind"]
        producer: int | str
        if check["kind"] == "check_run":
            producer = integer(check["app_id"], "check.app_id")
            if producer == 0:
                raise EvaluationError("app_id must be positive")
        elif check["kind"] == "status":
            producer = string(check["creator"], "check.creator")
        else:
            raise EvaluationError("unknown check kind")
        identity = (kind, name, producer)
        if identity in identities:
            raise EvaluationError("duplicate required check")
        identities.add(identity)
    return policy


def check_identity(check: RequiredCheck) -> tuple[str, str, int | str]:
    kind = check["kind"]
    producer = check["app_id"] if check["kind"] == "check_run" else check["creator"]
    return kind, check["name"], producer


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


def assess(facts: Observations, policy: Policy) -> Assessment:
    """欠落はINSUFFICIENT_DATA。規模やソース内容は必須条件を相殺しない。"""
    validate_policy(policy)
    result: Assessment = {
        "format": "pr-merge-readiness/report",
        "schema_version": 1,
        "mode": "shadow",
        "decision": "INSUFFICIENT_DATA",
        "conditions": [],
        "observations": facts,
        "policy": policy,
        "policy_sha256": hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest(),
    }

    def condition(name: str, status: ConditionStatus, detail: Any) -> None:
        result["conditions"].append({"name": name, "status": status, "detail": detail})

    try:
        if type(facts["schema_version"]) is not int or facts["schema_version"] != 1:
            raise EvaluationError("unsupported facts schema")
        string(facts["observed_at"], "observed_at")
        if facts["collection_errors"]:
            raise EvaluationError("collection incomplete: " + ", ".join(facts["collection_errors"]))
        pr = facts["pr"]
        head = sha(pr["head_sha"])
        sha(pr["base_sha"])
        if pr["merge_sha"] is not None:
            sha(pr["merge_sha"])
        if not boolean(facts["stable"], "stable"):
            condition(
                "freshness",
                "unknown",
                "PR, CI or reviews changed between samples; recollect",
            )
        else:
            condition("freshness", "pass", "PR, CI and reviews agree in both samples")
        if pr["state"] not in {"OPEN", "CLOSED", "MERGED"}:
            raise EvaluationError("unknown PR state")
        condition("open_pr", "pass" if pr["state"] == "OPEN" else "blocked", pr["state"])
        condition(
            "ready_for_review",
            "waiting" if boolean(pr["draft"], "draft") else "pass",
            pr["draft"],
        )
        mergeable = pr["mergeable"]
        if mergeable not in {"MERGEABLE", "CONFLICTING", "UNKNOWN"}:
            raise EvaluationError("unknown mergeable value")
        mergeable_status: dict[str, ConditionStatus] = {
            "MERGEABLE": "pass",
            "CONFLICTING": "blocked",
            "UNKNOWN": "waiting",
        }
        condition("mergeable", mergeable_status[mergeable], mergeable)
        review_decision = pr["review_decision"]
        if review_decision not in {
            None,
            "APPROVED",
            "CHANGES_REQUESTED",
            "REVIEW_REQUIRED",
        }:
            raise EvaluationError("unknown review decision")
        condition(
            "github_review",
            "blocked"
            if review_decision == "CHANGES_REQUESTED"
            else "waiting"
            if review_decision == "REVIEW_REQUIRED"
            else "pass",
            review_decision,
        )

        reviews = facts["reviews"]
        # 下書きの作成IDより投稿時刻を優先する。コメントだけでは判断を上書きしない。
        latest = {}
        published = [r for r in reviews if r["state"] != "PENDING"]
        for review in sorted(
            published,
            key=lambda r: (
                string(r["submitted_at"], "review.submitted_at"),
                integer(r["id"], "review.id"),
            ),
        ):
            review_state = review["state"]
            if review_state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED", "COMMENTED"}:
                raise EvaluationError("unknown review state")
            if review_state in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                latest[string(review["author"], "review.author")] = review
        changes = [r["id"] for r in latest.values() if r["state"] == "CHANGES_REQUESTED"]
        condition("change_requests", "blocked" if changes else "pass", changes)
        approvals = sum(
            r["state"] == "APPROVED" and r["commit_sha"] == head for r in latest.values()
        )
        condition(
            "current_head_approvals",
            "pass" if approvals >= policy["minimum_approvals"] else "waiting",
            {"actual": approvals, "required": policy["minimum_approvals"]},
        )
        days = policy["stale_change_review_days"]
        ages = change_ages(facts, days)
        stale = any(file["status"] == "stale" for file in ages)
        human_reviews = []
        if stale:
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
        unresolved = integer(facts["unresolved_threads"], "unresolved_threads")
        condition(
            "review_threads",
            "blocked" if policy["require_resolved_threads"] and unresolved else "pass",
            unresolved,
        )

        definitions = facts["ci_definition_changes"]
        if not isinstance(definitions, list):
            raise EvaluationError("ci_definition_changes: list required")
        for definition in definitions:
            string(definition, "ci_definition_changes entry")
        condition(
            "unchanged_ci_definitions",
            "blocked" if definitions else "pass",
            definitions,
        )

        checks = facts["checks"]
        for required in policy["required_checks"]:
            identity = check_identity(required)
            matching = [
                c
                for c in checks
                if check_identity(c) == identity and c["sha"] in {head, pr["merge_sha"]}
            ]
            # 現在のtest mergeに同名checkがあればそちらを優先し、head成功へ逃がさない。
            merged = [
                c for c in matching if pr["merge_sha"] is not None and c["sha"] == pr["merge_sha"]
            ]
            matching = merged or [c for c in matching if c["sha"] == head]
            label = "required_check:" + required["name"]
            if not matching:
                condition(label, "waiting", {"reason": "missing", "identity": identity})
                continue
            current = max(matching, key=lambda c: integer(c["id"], "check.id"))
            state: ConditionStatus
            if current["status"] in {
                "queued",
                "in_progress",
                "pending",
                "requested",
                "waiting",
            }:
                state = "waiting"
            elif current["status"] == "completed" and current["conclusion"] == "success":
                state = "pass"
            else:
                # neutral/skippedは実行成功と混同しない。
                state = "blocked"
            condition(label, state, current)

        # BLOCKEDは原因を列挙しない集約値。明示的な待機条件があれば、
        # その完了後に再評価する。CI失敗・変更要求などのblockedは引き続き優先する。
        merge_state = pr["merge_state"]
        has_waiting_condition = any(c["status"] == "waiting" for c in result["conditions"])
        status: ConditionStatus = (
            "pass"
            if merge_state == "CLEAN"
            else "waiting"
            if merge_state in {"UNKNOWN", "BEHIND", "DRAFT"}
            or (merge_state == "BLOCKED" and has_waiting_condition)
            else "blocked"
        )
        condition("github_merge_state", status, merge_state)

        # サイズと変更形態は観測のみ。テスト差分、パス、作者から安全性を推測しない。
        for key in ("additions", "deletions", "changed_files"):
            integer(facts["change"][key], key)
        states = {c["status"] for c in result["conditions"]}
        result["decision"] = (
            "INSUFFICIENT_DATA"
            if "unknown" in states
            else "HUMAN_REVIEW_REQUIRED"
            if "blocked" in states
            else "WAITING"
            if "waiting" in states
            else "SHADOW_CONDITIONS_MET"
        )
    except (KeyError, TypeError, ValueError) as error:
        condition("data_integrity", "unknown", str(error))
    return result
