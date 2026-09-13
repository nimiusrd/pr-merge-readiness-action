#!/usr/bin/env python3
"""GitHub APIのメタデータを収集する。PRのcheckout・ソース解析・書き込みは行わない。"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .contracts import (
    ChangedFile,
    ChangeHistory,
    DecisionMetadata,
    ObservationChange,
    Observations,
    PullRequest,
    file_history_path,
    sha,
    string,
    timestamp,
)

MAX_PAGES = 30
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_HISTORY_FILES = 100
MAX_HISTORY_REQUESTS = 100
PR_FIELDS = """
number state isDraft headRefOid baseRefOid baseRefName updatedAt
mergeable mergeStateStatus reviewDecision
additions deletions changedFiles
potentialMergeCommit { oid }
"""


class CollectionError(ValueError):
    """API失敗・打ち切りは情報不足として記録する。"""


class GitHub:
    def __init__(self, repository: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise CollectionError("invalid repository")
        self.repository = repository
        self.prefix = f"/repos/{repository}"
        self.owner, self.repo = repository.split("/")
        self.api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self.graphql_url = os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql")

    def request(self, path: str, body: dict | None = None):
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

    def pages(self, path: str, key: str | None = None) -> list:
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

    def graphql(self, number: int, selection: str, cursor: str | None = None) -> dict:
        query = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) { SELECTION }
  }
}
""".replace("SELECTION", selection)
        # PR state queryに未使用の変数宣言を残さない。
        if "$cursor" not in selection:
            query = query.replace(", $cursor: String", "")
        variables = {"owner": self.owner, "repo": self.repo, "number": number}
        if "$cursor" in selection:
            variables["cursor"] = cursor
        result = self.request("/graphql", {"query": query, "variables": variables})
        return result["data"]["repository"]["pullRequest"]

    def connection(self, number: int, name: str, fields: str) -> list:
        records = []
        cursor = None
        for _ in range(MAX_PAGES):
            result = self.graphql(
                number,
                f"{name}(first: 100, after: $cursor) {{ totalCount nodes {{ {fields} }} pageInfo {{ endCursor hasNextPage }} }}",
                cursor,
            )
            connection = result[name]
            records.extend(connection["nodes"])
            info = connection["pageInfo"]
            if info["hasNextPage"] is False:
                if connection["totalCount"] != len(records):
                    raise CollectionError("GraphQL collection count mismatch")
                return records
            next_cursor = info["endCursor"]
            if not next_cursor or next_cursor == cursor:
                raise CollectionError("invalid GraphQL cursor")
            cursor = next_cursor
        raise CollectionError("GraphQL pagination limit exceeded")


def normalized_pr(raw: dict) -> PullRequest:
    merge = raw["potentialMergeCommit"]
    return {
        "number": raw["number"],
        "state": raw["state"],
        "draft": raw["isDraft"],
        "head_sha": sha(raw["headRefOid"]),
        "base_sha": sha(raw["baseRefOid"]),
        "base_ref": raw["baseRefName"],
        "updated_at": raw["updatedAt"],
        "merge_sha": sha(merge["oid"]) if merge else None,
        "mergeable": raw["mergeable"],
        "merge_state": raw["mergeStateStatus"],
        "review_decision": raw["reviewDecision"],
        "additions": raw["additions"],
        "deletions": raw["deletions"],
        "changed_files": raw["changedFiles"],
    }


def decision_metadata(
    api: GitHub,
    number: int,
    pr: PullRequest,
) -> DecisionMetadata:
    """判定に使うCI・レビュー状態を同じ方法で再取得できるようにする。"""
    metadata = {}
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
    threads = api.connection(number, "reviewThreads", "isResolved")
    if any(type(t["isResolved"]) is not bool for t in threads):
        raise CollectionError("invalid review thread state")
    metadata["unresolved_threads"] = sum(not t["isResolved"] for t in threads)
    metadata["checks"] = []
    for commit in dict.fromkeys([pr["head_sha"], pr["merge_sha"]]):
        if commit is None:
            continue
        runs = api.pages(f"/commits/{commit}/check-runs?filter=all", "check_runs")
        for run in runs:
            # APIを要求したSHAとcheck自体のSHAの双方を保存・照合する。
            if run["head_sha"] != commit:
                raise CollectionError("check run SHA mismatch")
            metadata["checks"].append(
                {
                    "kind": "check_run",
                    "name": run["name"],
                    "app_id": run["app"]["id"],
                    "id": run["id"],
                    "sha": commit,
                    "status": run["status"],
                    "conclusion": run["conclusion"],
                }
            )
        for status in api.pages(f"/commits/{commit}/statuses"):
            metadata["checks"].append(
                {
                    "kind": "status",
                    "name": status["context"],
                    "creator": status["creator"]["login"],
                    "id": status["id"],
                    "sha": commit,
                    "status": "pending" if status["state"] == "pending" else "completed",
                    "conclusion": status["state"],
                }
            )
    for key in ("reviews", "checks"):
        metadata[key].sort(key=lambda item: json.dumps(item, sort_keys=True))
    return metadata


def observation_changes(before: dict, after: dict) -> list[ObservationChange]:
    """許可済みの正規化メタデータだけから、変更フィールドと前後値を残す。"""
    changes = []

    def compare(group, old, new, identity=None):
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
    compare(
        "unresolved_threads",
        {"count": before["unresolved_threads"]},
        {"count": after["unresolved_threads"]},
    )
    for group in ("reviews", "checks"):
        keys = ("id",) if group == "reviews" else ("kind", "sha", "id")
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
                # 名前とproducerも、差分の識別に必要な既存メタデータだけを使う。
                identity.update(
                    {
                        k: old[key][k]
                        for k in ("name", "app_id", "creator", "author")
                        if k in old[key]
                    }
                )
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
            record = {
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
                record.update(last_commit_sha=commit, last_changed_at=at)
            history["files"].append(record)
        return history


def collect(
    api: GitHub,
    number: int,
    *,
    history_collector: ChangeHistoryCollector | None = None,
) -> Observations:
    facts: Observations = {
        "schema_version": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "repository": api.repository,
        "collection_errors": [],
        "stable": False,
        "observation_changes": None,
    }
    try:
        before = normalized_pr(api.graphql(number, PR_FIELDS))
        facts["pr"] = before
        # RESTの旧pathも使い、workflowディレクトリ外へのrenameを取りこぼさない。
        # 同梱されるpatchやsource URLは参照・保存しない。
        files = []
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
        # GitHub Actionsの共通規約。コードベース固有の重要pathは持たない。
        facts["ci_definition_changes"] = sorted(
            {
                path
                for file in files
                for path in (file["path"], file["previous_path"])
                if path is not None and path.startswith(".github/workflows/")
            }
        )
        if any(
            facts["change"][key] != before[key]
            for key in ("changed_files", "additions", "deletions")
        ):
            raise CollectionError("PR file totals mismatch")
        initial_metadata = decision_metadata(
            api,
            number,
            before,
        )
        facts.update(initial_metadata)
        collector = (
            history_collector if history_collector is not None else ChangeHistoryCollector(api)
        )
        facts["change_history"] = collector.collect(before["base_sha"], files)
        histories = {}
        for check in facts["checks"]:
            key = (
                check["sha"],
                check["kind"],
                check["name"],
                check.get("app_id", check.get("creator")),
            )
            histories.setdefault(key, []).append(check)
        facts["ci_history"] = [
            {
                "sha": key[0],
                "kind": key[1],
                "name": key[2],
                "producer": key[3],
                "records": len(checks),
                "failure_before_success": any(
                    c["conclusion"] in {"failure", "error", "timed_out"} for c in checks
                )
                and max(checks, key=lambda c: c["id"])["conclusion"] == "success",
            }
            for key, checks in histories.items()
        ]
        confirmed_metadata = decision_metadata(
            api,
            number,
            before,
        )
        after = normalized_pr(api.graphql(number, PR_FIELDS))
        facts["rechecked"] = {
            "pr": before == after,
            **{key: initial_metadata[key] == confirmed_metadata[key] for key in initial_metadata},
        }
        facts["stable"] = all(facts["rechecked"].values())
        facts["observation_changes"] = observation_changes(
            {"pr": before, **initial_metadata},
            {"pr": after, **confirmed_metadata},
        )
        facts["observed_at"] = datetime.now(timezone.utc).isoformat()
    except (CollectionError, KeyError, TypeError, ValueError) as error:
        facts["collection_errors"].append(str(error))
    return facts


def targets(api: GitHub, event: dict, requested: int | None) -> list[int]:
    if requested is not None:
        if requested <= 0:
            raise CollectionError("PR number must be positive")
        return [requested]
    if "pull_request" in event:
        return [event["pull_request"]["number"]]
    # CI完了時も全open PRを再評価する。base更新・fork・同じheadを持つ複数PRに対応。
    # 古いworkflow_run payloadのSHAを、現在のPRのSHAとして使わない。
    return [p["number"] for p in api.pages("/pulls?state=open")]
