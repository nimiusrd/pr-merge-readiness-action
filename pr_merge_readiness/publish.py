#!/usr/bin/env python3
"""同じworkflow実行で観測した結果をPRラベルへ反映する。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .contracts import Assessment

MAX_PAGES = 30
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

DECISION_LABELS = {
    "SHADOW_CONDITIONS_MET": "shadow/要対応事項なし",
    "HUMAN_REVIEW_REQUIRED": "shadow/要対応",
    "INSUFFICIENT_DATA": "shadow/再観測が必要",
}
# 自動更新または手動同期で対象PRから旧表示を取り除く。旧ラベルは作成しない。
RETIRED_LABELS = {
    "shadow/レビュー待ち",
    "shadow/要マージ判断",
    "shadow/CI・レビュー待ち",
    "shadow/レビュー条件充足",
}
MANAGED_LABELS = set(DECISION_LABELS.values()) | RETIRED_LABELS
LABEL_COLORS = {
    "shadow/要対応事項なし": "0E8A16",
    "shadow/要対応": "D93F0B",
    "shadow/再観測が必要": "BFD4F2",
}
LABEL_DESCRIPTIONS = {
    "shadow/要対応事項なし": (
        "変更履歴に基づく追加確認事項はありません。GitHubのマージ条件は別途確認してください。"
        "詳細はActionsのPR Merge Readiness。"
    ),
    "shadow/要対応": (
        "変更履歴に基づく追加の人間レビューが必要。詳細はActionsのPR Merge Readiness。"
    ),
    "shadow/再観測が必要": (
        "追加確認の観測が失敗、または対象・承認情報が変化。"
        "ActionsのPR Merge Readinessを手動で再実行する。"
    ),
}


class PublishError(ValueError):
    """ラベル書き込みや対象PRの再取得失敗。"""


class GitHub:
    def __init__(self, repository: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise PublishError("invalid repository")
        self.repository = repository
        self.prefix = f"/repos/{repository}"
        self.api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    def request(
        self, path: str, body: dict[str, Any] | None = None, method: str | None = None
    ) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "autonomous-merge-shadow-publisher",
        }
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        verb = method or ("POST" if body is not None else "GET")
        request = Request(
            self.api_url + path,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
            method=verb,
        )
        try:
            with urlopen(request, timeout=30) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise PublishError("API response exceeds limit")
            if not data:
                return None
            return json.loads(data)
        except PublishError:
            raise
        except HTTPError as error:
            raise PublishError(f"API {verb} {path}: HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError, ValueError) as error:
            raise PublishError(f"API {verb} {path}: {type(error).__name__}") from None

    def pages(self, path: str) -> list[dict[str, Any]]:
        records = []
        separator = "&" if "?" in path else "?"
        for page in range(1, MAX_PAGES + 1):
            result = self.request(f"{self.prefix}{path}{separator}per_page=100&page={page}")
            batch = result
            if not isinstance(batch, list):
                raise PublishError("invalid REST page")
            records.extend(batch)
            if len(batch) < 100:
                return records
        raise PublishError("REST pagination limit exceeded")


def encoded_label(name: str) -> str:
    return quote(name, safe="")


def ensure_labels(api: GitHub) -> None:
    for name in DECISION_LABELS.values():
        path = f"{api.prefix}/labels/{encoded_label(name)}"
        definition = {
            "name": name,
            "color": LABEL_COLORS[name],
            "description": LABEL_DESCRIPTIONS[name],
        }
        try:
            current = api.request(path)
        except PublishError as error:
            if "HTTP 404" not in str(error):
                raise
            try:
                current = api.request(f"{api.prefix}/labels", definition)
            except PublishError as created:
                if "HTTP 422" not in str(created):
                    raise
                current = api.request(path)
        if (
            current.get("description") != definition["description"]
            or str(current.get("color", "")).lower() != definition["color"].lower()
        ):
            api.request(
                path,
                {"color": definition["color"], "description": definition["description"]},
                method="PATCH",
            )


def is_publication_target(pr: dict[str, Any]) -> bool:
    """同一リポジトリからのPRを対象とし、DependabotのPRを除外する。"""
    head_repository = pr["head"].get("repo")
    return (
        head_repository is not None
        and head_repository["id"] == pr["base"]["repo"]["id"]
        and pr["user"]["login"] != "dependabot[bot]"
    )


def desired_label(
    report: Assessment, current: dict[str, Any], repository: str, number: int
) -> str | None:
    """fork・Dependabot以外のopen PRへ付与し、対象外のPRは管理ラベルを清掃する。"""
    decision = report.get("decision")
    if not isinstance(decision, str) or decision not in DECISION_LABELS:
        raise PublishError(f"unknown label decision: {decision}")
    facts: Mapping[str, Any] = report.get("observations") or {}
    observed: Mapping[str, Any] = facts.get("pr") or {}
    if facts.get("repository") != repository:
        raise PublishError("report repository mismatch")
    if observed.get("number", number) != number:
        raise PublishError("report PR mismatch")
    if current["state"] == "closed":
        return None
    if not is_publication_target(current):
        return None
    expected = {
        "head_sha": current["head"]["sha"],
        "base_sha": current["base"]["sha"],
        "base_ref": current["base"]["ref"],
    }
    if any(observed.get(key) != value for key, value in expected.items()):
        return DECISION_LABELS["INSUFFICIENT_DATA"]
    return DECISION_LABELS[decision]


def sync_labels(api: GitHub, number: int, names: list[str], desired: str | None) -> str:
    """単一writerを前提に追加→削除する。中断時の不整合は次回更新で修復する。"""
    changed = False
    if desired is not None and desired not in names:
        api.request(f"{api.prefix}/issues/{number}/labels", {"labels": [desired]})
        changed = True
    for name in names:
        if name not in MANAGED_LABELS or name == desired:
            continue
        try:
            api.request(
                f"{api.prefix}/issues/{number}/labels/{encoded_label(name)}",
                method="DELETE",
            )
        except PublishError as error:
            if "HTTP 404" not in str(error):
                raise
        changed = True
    return "updated" if changed else "unchanged"


def publish_pr(
    api: GitHub, number: int, report: Assessment, *, expected_head: str | None = None
) -> str:
    current = api.request(f"{api.prefix}/pulls/{number}")
    if expected_head is not None and current["head"]["sha"] != expected_head:
        return "skipped"
    desired = desired_label(report, current, api.repository, number)
    return sync_labels(api, number, [item["name"] for item in current["labels"]], desired)


def cleanup_closed(api: GitHub) -> None:
    # closed全履歴は走査せず、管理ラベルが残るPRだけを回収する。
    numbers = set()
    for label in sorted(MANAGED_LABELS):
        for issue in api.pages(f"/issues?state=closed&labels={encoded_label(label)}"):
            if "pull_request" in issue:
                numbers.add(issue["number"])
    for number in sorted(numbers):
        current = api.request(f"{api.prefix}/pulls/{number}")
        if current["state"] == "closed":
            sync_labels(api, number, [item["name"] for item in current["labels"]], None)
