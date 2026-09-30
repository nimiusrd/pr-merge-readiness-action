/** 判定済みの正規化メタデータだけから Summary を生成する。 */
import type { Assessment } from "./contracts.js";
export function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#x27;");
}
export function markdown(result: Assessment): string {
  const safe = (value: unknown) =>
    "<code>" + escapeHtml(JSON.stringify(value ?? null)) + "</code>";
  const facts = result.observations;
  const lines = [
    "## PR Merge Readiness",
    "",
    "変更履歴に基づく追加確認の観測時点の判定です。承認数・会話解決・CI などのマージ条件は GitHub Ruleset で確認してください。レビュー完了やマージ許可を示すものではありません。",
    "",
    "追加確認の判定: " + safe(result.decision),
    "",
    "観測時刻: " + safe(facts.observed_at),
    "",
    "対象: " + safe(facts.pr),
    "",
    "変更量・形態（判定には加点しない）: " + safe(facts.change),
    "",
    "前回変更からの経過日数の閾値（超過時は現在headへの人間の承認が必要）: " +
      safe(result.policy.stale_change_review_days),
    "",
  ];
  for (const condition of result.conditions) lines.push("- " + safe(condition));
  lines.push("", "### 観測間の変化", "");
  if (facts.observation_changes == null)
    lines.push("差分情報なし（再取得未完了）。");
  else if (!facts.observation_changes.length)
    lines.push("比較した正規化メタデータに変化はありません。");
  else
    for (const change of facts.observation_changes)
      lines.push("- " + safe(change));
  return lines.join("\n") + "\n";
}
