/** 同じ実行で観測した結果を PR ラベルへ反映する。 */
import { PublishError } from "./api.js";
import type { Writer } from "./api.js";
import { errorMessage, integer, object, string } from "./contracts.js";
import type { Assessment, JsonObject } from "./contracts.js";
export const DECISION_LABELS = {
  SHADOW_CONDITIONS_MET: "shadow/要対応事項なし",
  HUMAN_REVIEW_REQUIRED: "shadow/要対応",
  INSUFFICIENT_DATA: "shadow/再観測が必要",
} as const;
export const RETIRED_LABELS = [
  "shadow/レビュー待ち",
  "shadow/要マージ判断",
  "shadow/CI・レビュー待ち",
  "shadow/レビュー条件充足",
];
export const MANAGED_LABELS = new Set([
  ...Object.values(DECISION_LABELS),
  ...RETIRED_LABELS,
]);
export const LABEL_COLORS: Record<string, string> = {
  "shadow/要対応事項なし": "0E8A16",
  "shadow/要対応": "D93F0B",
  "shadow/再観測が必要": "BFD4F2",
};
export const LABEL_DESCRIPTIONS: Record<string, string> = {
  "shadow/要対応事項なし":
    "変更履歴に基づく追加確認事項はありません。GitHubのマージ条件は別途確認してください。詳細はActionsのPR Merge Readiness。",
  "shadow/要対応":
    "変更履歴に基づく追加の人間レビューが必要。詳細はActionsのPR Merge Readiness。",
  "shadow/再観測が必要":
    "追加確認の観測が失敗、または対象・承認情報が変化。ActionsのPR Merge Readinessを手動で再実行する。",
};
export async function ensureLabels(api: Writer): Promise<void> {
  for (const name of Object.values(DECISION_LABELS)) {
    const path = `${api.prefix}/labels/${encodeURIComponent(name)}`;
    const definition = {
      name,
      color: LABEL_COLORS[name]!,
      description: LABEL_DESCRIPTIONS[name]!,
    };
    let current: JsonObject;
    try {
      current = object(await api.request(path));
    } catch (error) {
      if (
        !(error instanceof PublishError) ||
        !error.message.includes("HTTP 404")
      )
        throw error;
      try {
        current = object(await api.request(`${api.prefix}/labels`, definition));
      } catch (created) {
        if (
          !(created instanceof PublishError) ||
          !created.message.includes("HTTP 422")
        )
          throw created;
        current = object(await api.request(path));
      }
    }
    if (
      current.description !== definition.description ||
      String(current.color ?? "").toLowerCase() !==
        definition.color.toLowerCase()
    )
      await api.request(
        path,
        { color: definition.color, description: definition.description },
        "PATCH",
      );
  }
}
export function isPublicationTarget(pr: JsonObject): boolean {
  const headRepo = object(pr.head).repo;
  return (
    headRepo != null &&
    object(headRepo).id === object(object(pr.base).repo).id &&
    object(pr.user).login !== "dependabot[bot]"
  );
}
export function desiredLabel(
  report: Assessment,
  current: JsonObject,
  repository: string,
  number: number,
): string | null {
  if (!Object.hasOwn(DECISION_LABELS, report.decision))
    throw new PublishError(
      `unknown label decision: ${String(report.decision)}`,
    );
  const facts = report.observations;
  const observed = facts?.pr;
  if (facts?.repository !== repository)
    throw new PublishError("report repository mismatch");
  if ((observed?.number ?? number) !== number)
    throw new PublishError("report PR mismatch");
  if (current.state === "closed" || !isPublicationTarget(current)) return null;
  const expected = {
    head_sha: object(current.head).sha,
    base_sha: object(current.base).sha,
    base_ref: object(current.base).ref,
  };
  if (
    (Object.keys(expected) as (keyof typeof expected)[]).some(
      (key) => observed?.[key] !== expected[key],
    )
  )
    return DECISION_LABELS.INSUFFICIENT_DATA;
  return DECISION_LABELS[report.decision];
}
export async function syncLabels(
  api: Writer,
  number: number,
  names: string[],
  desired: string | null,
): Promise<string> {
  let changed = false;
  if (desired !== null && !names.includes(desired)) {
    await api.request(`${api.prefix}/issues/${number}/labels`, {
      labels: [desired],
    });
    changed = true;
  }
  for (const name of names) {
    if (!MANAGED_LABELS.has(name) || name === desired) continue;
    try {
      await api.request(
        `${api.prefix}/issues/${number}/labels/${encodeURIComponent(name)}`,
        undefined,
        "DELETE",
      );
    } catch (error) {
      if (
        !(error instanceof PublishError) ||
        !errorMessage(error).includes("HTTP 404")
      )
        throw error;
    }
    changed = true;
  }
  return changed ? "updated" : "unchanged";
}
function labelNames(current: JsonObject): string[] {
  if (!Array.isArray(current.labels)) throw new PublishError("invalid labels");
  return current.labels.map((item) => string(object(item).name, "label.name"));
}
export async function publishPr(
  api: Writer,
  number: number,
  report: Assessment,
  expectedHead?: string,
): Promise<string> {
  const current = object(await api.request(`${api.prefix}/pulls/${number}`));
  if (expectedHead !== undefined && object(current.head).sha !== expectedHead)
    return "skipped";
  return syncLabels(
    api,
    number,
    labelNames(current),
    desiredLabel(report, current, api.repository, number),
  );
}
export async function cleanupClosed(api: Writer): Promise<void> {
  const numbers = new Set<number>();
  for (const label of [...MANAGED_LABELS].sort()) {
    for (const issue of await api.pages(
      `/issues?state=closed&labels=${encodeURIComponent(label)}`,
    )) {
      if (Object.hasOwn(issue, "pull_request"))
        numbers.add(integer(issue.number, "PR number"));
    }
  }
  for (const number of [...numbers].sort((a, b) => a - b)) {
    const current = object(await api.request(`${api.prefix}/pulls/${number}`));
    if (current.state === "closed")
      await syncLabels(api, number, labelNames(current), null);
  }
}
