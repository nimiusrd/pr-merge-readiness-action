#!/usr/bin/env python3
"""GitHub APIのメタデータを収集する。PRのcheckout・ソース解析・書き込みは行わない。"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .contracts import (
    ChangedFile,
    ChangeHistory,
    DecisionMetadata,
    FileHistory,
    ObservationChange,
    Observations,
    PullRequest,
    Policy,
    file_history_path,
    sha,
    string,
    timestamp,
)

from .evaluate import change_ages

MAX_PAGES = 30
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_HISTORY_FILES = 100
MAX_HISTORY_REQUESTS = 100
PR_FIELDS = """
number state headRefOid baseRefOid baseRefName updatedAt
additions deletions changedFiles
"""


class CollectionError(ValueError):
    """API失敗・打ち切りは情報不足として記録する。"""


class ProposalHeadChanged(CollectionError):
    """未検証のheadへ観測対象が変わったため、PRの判定・公開を中止する。"""


class GitHub:
    def __init__(self, repository: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise CollectionError("invalid repository")
        self.repository = repository
        self.prefix = f"/repos/{repository}"
        self.owner, self.repo = repository.split("/")
        self.api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self.graphql_url = os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql")

    def request(self, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pr-merge-readiness",
        }
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = self.graphql_url if path == "/graphql" else self.api_url + path
        request = Request(
            url,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            with urlopen(request, timeout=30) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise CollectionError("API response exceeds limit")
            result = json.loads(data)
        except CollectionError:
            raise
        except HTTPError as error:
            raise CollectionError(f"API {path}: HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError, ValueError) as error:
            raise CollectionError(f"API {path}: {type(error).__name__}") from None
        if isinstance(result, dict) and result.get("errors"):
            raise CollectionError("GraphQL returned errors")
        return result

    def pages(self, path: str, key: str | None = None) -> list[dict[str, Any]]:
        records = []
        separator = "&" if "?" in path else "?"
        for page in range(1, MAX_PAGES + 1):
            result = self.request(f"{self.prefix}{path}{separator}per_page=100&page={page}")
            batch = result[key] if key else result
            if not isinstance(batch, list):
                raise CollectionError("invalid REST page")
            records.extend(batch)
            if len(batch) < 100:
                if key and result.get("total_count") != len(records):
                    raise CollectionError("REST collection count mismatch")
                return records
        raise CollectionError("REST pagination limit exceeded")

    def graphql(self, number: int, selection: str) -> dict[str, Any]:
        query = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) { SELECTION }
  }
}
""".replace("SELECTION", selection)
        variables = {"owner": self.owner, "repo": self.repo, "number": number}
        result = self.request("/graphql", {"query": query, "variables": variables})
        return cast(dict[str, Any], result["data"]["repository"]["pullRequest"])


def normalized_pr(raw: dict[str, Any]) -> PullRequest:
    return {
        "number": raw["number"],
        "state": raw["state"],
        "head_sha": sha(raw["headRefOid"]),
        "base_sha": sha(raw["baseRefOid"]),
        "base_ref": raw["baseRefName"],
        "updated_at": raw["updatedAt"],
        "additions": raw["additions"],
        "deletions": raw["deletions"],
        "changed_files": raw["changedFiles"],
    }


def read_pr(api: GitHub, number: int) -> PullRequest:
    return normalized_pr(api.graphql(number, PR_FIELDS))


def decision_metadata(
    api: GitHub,
    number: int,
) -> DecisionMetadata:
    """判定に使うレビュー状態を同じ方法で再取得できるようにする。"""
    metadata: DecisionMetadata = {"reviews": []}
    reviews = api.pages(f"/pulls/{number}/reviews")
    metadata["reviews"] = [
        {
            "id": r["id"],
            "author": (r["user"] or {}).get("login"),
            "state": r["state"],
            "commit_sha": r["commit_id"],
            "submitted_at": r["submitted_at"],
            "author_type": (r["user"] or {}).get("type"),
            "author_association": r.get("author_association"),
        }
        for r in reviews
    ]
    metadata["reviews"].sort(key=lambda item: json.dumps(item, sort_keys=True))
    return metadata


def observation_changes(before: dict[str, Any], after: dict[str, Any]) -> list[ObservationChange]:
    """許可済みの正規化メタデータだけから、変更フィールドと前後値を残す。"""
    changes: list[ObservationChange] = []

    def compare(
        group: str,
        old: Mapping[str, Any],
        new: Mapping[str, Any],
        identity: dict[str, Any] | None = None,
    ) -> None:
        for field in sorted(old.keys() | new.keys()):
            if old.get(field) != new.get(field):
                changes.append(
                    {
                        "group": group,
                        "identity": identity,
                        "field": field,
                        "before": old.get(field),
                        "after": new.get(field),
                    }
                )

    compare("pr", before["pr"], after["pr"])
    for group in ("reviews",):
        keys = ("id",)
        old = {tuple(r[k] for k in keys): r for r in before[group]}
        new = {tuple(r[k] for k in keys): r for r in after[group]}
        for key in sorted(old.keys() | new.keys()):
            identity = dict(zip(keys, key))
            if key not in old or key not in new:
                changes.append(
                    {
                        "group": group,
                        "identity": identity,
                        "field": "record",
                        "before": old.get(key),
                        "after": new.get(key),
                    }
                )
            else:
                # 本文を含めず、差分の識別に必要な作者だけを使う。
                identity.update({k: old[key][k] for k in ("author",) if k in old[key]})
                compare(group, old[key], new[key], identity)
    return changes


class ChangeHistoryCollector:
    """同じrun・リポジトリの全PRで履歴照会の予算と成功結果を共有する。"""

    def __init__(self, api: GitHub):
        self.api = api
        self.requests = 0
        self.cache: dict[tuple[str, str], tuple[str, str]] = {}

    def collect(self, base_sha: str, files: list[ChangedFile]) -> ChangeHistory:
        base = sha(base_sha)
        paths = [(file["path"], file_history_path(file)) for file in files]
        if sum(path is not None for _, path in paths) > MAX_HISTORY_FILES:
            raise CollectionError("change history file limit exceeded")
        missing = {
            (base, previous) for _, previous in paths if previous is not None
        } - self.cache.keys()
        if self.requests + len(missing) > MAX_HISTORY_REQUESTS:
            # 完了できないPRには残り予算を使わず、後続PRの取得余地を残す。
            raise CollectionError(
                f"change history run request limit exceeded: "
                f"{self.requests}/{MAX_HISTORY_REQUESTS} used, {len(missing)} needed"
            )
        history: ChangeHistory = {"base_sha": base, "files": []}
        for path, previous in paths:
            record: FileHistory = {
                "path": path,
                "history_path": previous,
                "last_commit_sha": None,
                "last_changed_at": None,
            }
            if previous is not None:
                key = (base, previous)
                if key not in self.cache:
                    # base SHAで固定する。PR headで日数をリセットせず、base更新時は再取得する。
                    query = urlencode({"sha": base, "path": previous, "per_page": 1})
                    # 失敗した要求もrun全体の上限に含める。
                    self.requests += 1
                    commits = self.api.request(f"{self.api.prefix}/commits?{query}")
                    if not isinstance(commits, list) or len(commits) != 1:
                        raise CollectionError(f"last change unavailable: {previous}")
                    last = commits[0]
                    at = last["commit"]["committer"]["date"]
                    timestamp(at, "last_changed_at")
                    self.cache[key] = (sha(last["sha"]), at)
                commit, at = self.cache[key]
                record.update({"last_commit_sha": commit, "last_changed_at": at})
            history["files"].append(record)
        return history


def collect(
    api: GitHub,
    number: int,
    *,
    policy: Policy,
    history_collector: ChangeHistoryCollector | None = None,
    expected_head: str | None = None,
) -> Observations:
    facts: Observations = {
        "schema_version": 3,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "repository": api.repository,
        "collection_errors": [],
        "stable": False,
        "observation_changes": None,
    }
    try:
        before = read_pr(api, number)
        if expected_head is not None and before["head_sha"] != expected_head:
            raise ProposalHeadChanged("PR head differs from validated proposal before observation")
        facts["pr"] = before
        # rename前のpathを使い、baseでの変更履歴を取得する。
        # 同梱されるpatchやsource URLは参照・保存しない。
        files: list[ChangedFile] = []
        for raw in api.pages(f"/pulls/{number}/files"):
            previous = (
                string(raw["previous_filename"], "previous_filename")
                if raw["status"] == "renamed"
                else None
            )
            files.append(
                {
                    "path": string(raw["filename"], "filename"),
                    "previous_path": previous,
                    "changeType": "DELETED"
                    if raw["status"] == "removed"
                    else string(raw["status"], "file.status").upper(),
                    "additions": raw["additions"],
                    "deletions": raw["deletions"],
                }
            )
        if len({f["path"] for f in files}) != len(files):
            raise CollectionError("duplicate file metadata")
        facts["change"] = {
            "changed_files": len(files),
            "additions": sum(f["additions"] for f in files),
            "deletions": sum(f["deletions"] for f in files),
            "types": dict(Counter(f["changeType"] for f in files)),
            # APIで確定できない情報は、0や「通常ファイル」に置き換えない。
            "binary_files": None,
            "mode_changes": None,
        }
        facts["files"] = files
        if any(
            facts["change"][key] != before[key]
            for key in ("changed_files", "additions", "deletions")
        ):
            raise CollectionError("PR file totals mismatch")
        collector = (
            history_collector if history_collector is not None else ChangeHistoryCollector(api)
        )
        facts["change_history"] = collector.collect(before["base_sha"], files)
        facts["observed_at"] = datetime.now(timezone.utc).isoformat()
        needs_review = any(
            file["status"] == "stale"
            for file in change_ages(facts, policy["stale_change_review_days"])
        )
        # 履歴条件が承認を要求する場合だけレビューを読む。
        initial_metadata = decision_metadata(api, number) if needs_review else {"reviews": []}
        facts["reviews"] = initial_metadata["reviews"]
        confirmed_metadata = decision_metadata(api, number) if needs_review else {"reviews": []}
        after = read_pr(api, number)
        if expected_head is not None and after["head_sha"] != expected_head:
            raise ProposalHeadChanged(
                "PR head changed after proposal validation during observation"
            )
        # PRの表示状態・更新時刻は追加確認の入力に含めない。
        target_fields = (
            "number",
            "head_sha",
            "base_sha",
            "base_ref",
            "additions",
            "deletions",
            "changed_files",
        )
        facts["rechecked"] = {
            "pr": all(before.get(key) == after.get(key) for key in target_fields),
            "reviews": initial_metadata["reviews"] == confirmed_metadata["reviews"],
        }
        facts["stable"] = all(facts["rechecked"].values())
        facts["observation_changes"] = observation_changes(
            {"pr": before, **initial_metadata},
            {"pr": after, **confirmed_metadata},
        )
        facts["observed_at"] = datetime.now(timezone.utc).isoformat()
        if not needs_review and any(
            file["status"] == "stale"
            for file in change_ages(facts, policy["stale_change_review_days"])
        ):
            raise CollectionError("history review threshold crossed during observation")
    except ProposalHeadChanged:
        raise
    except (CollectionError, KeyError, TypeError, ValueError) as error:
        facts["collection_errors"].append(str(error))
    return facts


def targets(api: GitHub, event: dict[str, Any], requested: int | None) -> list[int]:
    if requested is not None:
        if requested <= 0:
            raise CollectionError("PR number must be positive")
        return [requested]
    if "pull_request" in event:
        return [event["pull_request"]["number"]]
    # 番号未指定の手動観測では、現在の全open PRを対象にする。
    return [p["number"] for p in api.pages("/pulls?state=open")]
