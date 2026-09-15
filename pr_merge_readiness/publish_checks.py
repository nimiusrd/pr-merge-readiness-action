#!/usr/bin/env python3
"""観測結果への導線をneutral Checkとして公開する。評価・ラベル更新はしない。"""

from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from .contracts import Assessment

from .publish import DECISION_LABELS, GitHub, MAX_PAGES, PublishError, is_publication_target

CHECK_PREFIX = "Autonomous Merge Shadow / PR #"
EXTERNAL_PREFIX = "pr-merge-readiness-v1"
ACTIONS_APP_ID = 15368


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise PublishError("timestamp must have a timezone")
    return result


def positive(value: Any) -> int:
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise PublishError("positive integer required")
    return int(value)


def snapshot(pr: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": positive(pr["number"]),
        "head_sha": pr["head"]["sha"],
        "base_sha": pr["base"]["sha"],
        "base_ref": pr["base"]["ref"],
        "state": "MERGED" if pr.get("merged_at") else pr["state"].upper(),
        "draft": pr["draft"],
        "updated_at": pr["updated_at"],
    }


def code(value: object) -> str:
    return "<code>" + html.escape(str(value)) + "</code>"


def state_hash(current: dict[str, Any]) -> str:
    # タイトル・本文・コメントでも変わる時刻は、遅延イベントの失効判定に使わない。
    # 観測と公開直前の鮮度比較ではsnapshotのupdated_atを引き続き照合する。
    state = {key: value for key, value in current.items() if key != "updated_at"}
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def check_time(record: dict[str, Any]) -> datetime:
    return timestamp(record["external_id"].split(":", 2)[2].split("|", 1)[0])


def managed_checks(api: GitHub, number: int, head: str) -> list[dict[str, Any]]:
    """現在SHAの自分のCheckだけを読む。workflow/run履歴は走査しない。"""
    records = []
    name = CHECK_PREFIX + str(number)
    for page in range(1, MAX_PAGES + 1):
        result = api.request(
            f"{api.prefix}/commits/{head}/check-runs?filter=all"
            f"&check_name={quote(name, safe='')}&per_page=100&page={page}"
        )
        batch = result["check_runs"]
        if not isinstance(batch, list):
            raise PublishError("invalid Check page")
        records.extend(batch)
        if len(batch) < 100:
            if result["total_count"] != len(records):
                raise PublishError("Check collection count mismatch")
            break
    else:
        raise PublishError("Check pagination limit exceeded")
    return [
        record
        for record in records
        if record["name"] == name
        and record["head_sha"] == head
        and record["app"]["id"] == ACTIONS_APP_ID
        and (record.get("external_id") or "").startswith(f"{EXTERNAL_PREFIX}:{number}:")
    ]


def write_check(
    api: GitHub, current: dict[str, Any], at: str, title: str, summary: str, run_url: str
) -> str:
    number = current["number"]
    incoming = timestamp(at)
    existing = managed_checks(api, number, current["head_sha"])
    # 一度だけ作成したCheckを更新する。異常な既存メタデータは黙って上書きしない。
    if existing:
        latest = max(existing, key=check_time)
        if check_time(latest) >= incoming and latest["external_id"].endswith(
            "|" + state_hash(current)
        ):
            return "newer_or_same_observation_kept"
        # 同じheadでもbase等が変わった場合は古い表示を失効させる。
        # その際にも時刻のwatermarkは戻さない。
        incoming = max(incoming, check_time(latest))
    else:
        latest = None
    # 一覧取得後にもPRを確認し、head/base更新との競合で誤った結果を書かない。
    confirmed_pr = api.request(f"{api.prefix}/pulls/{number}")
    if not is_publication_target(confirmed_pr):
        return "skipped"
    confirmed = snapshot(confirmed_pr)
    if current != confirmed:
        raise PublishError("PR changed before Check publication; recollect")
    body = {
        "name": CHECK_PREFIX + str(number),
        "status": "completed",
        "conclusion": "neutral",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "external_id": f"{EXTERNAL_PREFIX}:{number}:{incoming.isoformat()}|{state_hash(current)}",
        "details_url": run_url,
        "output": {
            "title": title,
            "summary": summary,
        },
    }
    if latest:
        api.request(f"{api.prefix}/check-runs/{latest['id']}", body, method="PATCH")
    else:
        api.request(f"{api.prefix}/check-runs", {**body, "head_sha": current["head_sha"]})
    return "published"


def heading(current: dict[str, Any], run_url: str) -> str:
    return (
        "PR・レビュー・変更履歴の参考表示です。CI の結果は GitHub Checks で確認してください。自動マージ許可ではありません。\n\n"
        f"PR: #{current['number']} / 表示対象head: {code(current['head_sha'])}\n\n"
        f"現在のbase: {code(current['base_ref'])} {code(current['base_sha'])}\n\n"
        f"[このrun・attemptのSummaryとArtifacts]({run_url})\n\n"
    )


def publish_report(
    api: GitHub,
    number: int,
    report: Assessment,
    run_url: str,
    artifact: str,
    *,
    expected_head: str | None = None,
) -> str:
    decision = report["decision"]
    facts = report["observations"]
    observed: Mapping[str, Any] = facts.get("pr") or {}
    if decision not in DECISION_LABELS or facts["repository"] != api.repository:
        raise PublishError("report decision or repository mismatch")
    if observed.get("number", number) != number:
        raise PublishError("report PR mismatch")
    at = facts["observed_at"]
    timestamp(at)
    pr = api.request(f"{api.prefix}/pulls/{number}")
    if not is_publication_target(pr):
        return "skipped"
    if expected_head is not None and pr["head"]["sha"] != expected_head:
        return "skipped"
    current = snapshot(pr)
    agrees = all(observed.get(key) == value for key, value in current.items())
    old_head = bool(observed.get("head_sha") and observed["head_sha"] != current["head_sha"])
    if old_head:
        title = "未観測：古いSHAの記録（現在headは再観測が必要）"
    elif not agrees or decision == "INSUFFICIENT_DATA":
        title = "再観測が必要：INSUFFICIENT_DATA"
    else:
        title = "観測済み：" + decision
    summary = heading(current, run_url)
    source = report.get("provenance")
    if not agrees:
        summary += "保存した観測と公開時のPR状態が一致しません。以下は過去の記録です。\n\n"
    summary += (
        f"観測時刻: {code(at)}\n\n"
        f"観測head: {code(observed.get('head_sha'))}\n\n"
        f"観測base: {code(observed.get('base_sha'))}\n\n"
        f"評価器SHA: {code(source['evaluator']['sha'] if source else None)}\n\n"
        f"Policy SHA-256: {code(report['policy_sha256'])}\n\n"
        f"保存した判定: {code(decision)}\n\n"
        f"Artifact: {code(artifact)} / {code(f'pr-{number}.json')}\n\n"
    )
    for condition in report["conditions"]:
        summary += "- " + code(json.dumps(condition, ensure_ascii=False)) + "\n"
    summary += "\n再観測はPR Merge ReadinessのRun workflowでPR番号を指定してください。\n"
    # GitHubのsummary上限に収め、詳細は完全なartifactへ誘導する。
    if len(summary.encode()) > 60000:
        summary = summary.encode()[:58000].decode("utf-8", errors="ignore")
        summary += "\n\n条件詳細は上記artifactを参照してください。\n"
    return write_check(api, current, at, title, summary, run_url)
