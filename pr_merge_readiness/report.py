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
        "## PR Merge Readiness",
        "",
        "変更履歴に基づく追加確認の観測時点の判定です。承認数・会話解決・CI などのマージ条件は GitHub Ruleset で確認してください。レビュー完了やマージ許可を示すものではありません。",
        "",
        "追加確認の判定: " + safe(result["decision"]),
        "",
        "観測時刻: " + safe(facts.get("observed_at")),
        "",
        "対象: " + safe(facts.get("pr")),
        "",
        "変更量・形態（判定には加点しない）: " + safe(facts.get("change")),
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
        lines.append("差分情報なし（再取得未完了）。")
    elif not changes:
        lines.append("比較した正規化メタデータに変化はありません。")
    else:
        for change in changes:
            lines.append("- " + safe(change))
    return "\n".join(lines) + "\n"
