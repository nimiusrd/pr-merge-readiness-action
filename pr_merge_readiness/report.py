"""判定済みの結果からSummaryを生成する。収集・判定・書込みは行わない。"""

from __future__ import annotations

import html
import json
from typing import Any

from .contracts import Assessment


def markdown(result: Assessment) -> str:
    def safe(value: Any) -> str:
        return "<code>" + html.escape(json.dumps(value, ensure_ascii=False)) + "</code>"

    facts = result["observations"]
    lines = [
        "## Autonomous Merge Shadow",
        "",
        "観測時点の仮判定です。マージ許可・安全性の証明には使用しません。",
        "",
        "判定: " + safe(result["decision"]),
        "",
        "観測時刻: " + safe(facts.get("observed_at")),
        "",
        "対象: " + safe(facts.get("pr")),
        "",
        "変更量・形態（判定には加点しない）: " + safe(facts.get("change")),
        "",
        "CI履歴（不安定さの原因は推測しない）: " + safe(facts.get("ci_history")),
        "",
        "評価器: " + safe(result.get("provenance", {}).get("evaluator")),
        "設定: " + safe(result.get("provenance", {}).get("config")),
        "",
        "Policy SHA-256: " + safe(result["policy_sha256"]),
        "",
    ]
    if "stale_change_review_days" in result["policy"]:
        lines.extend(
            [
                "前回変更からの経過日数の閾値（超過時は現在headへの人間の承認が必要）: "
                + safe(result["policy"]["stale_change_review_days"]),
                "",
            ]
        )
    for item in result["conditions"]:
        lines.append("- " + safe(item))
    lines.extend(["", "### 観測間の変化", ""])
    changes = facts.get("observation_changes")
    if changes is None:
        lines.append("差分情報なし（再取得未完了、または旧形式の観測）。")
    elif not changes:
        lines.append("比較した正規化メタデータに変化はありません。")
    else:
        for change in changes:
            lines.append("- " + safe(change))
    return "\n".join(lines) + "\n"
