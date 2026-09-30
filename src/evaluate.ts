/** 変更履歴に基づく追加確認だけを判定する。マージ条件は GitHub に委ねる。 */
import {
  boolean,
  EvaluationError,
  fileHistoryPath,
  integer,
  object,
  sha,
  string,
  timestamp,
} from "./contracts.js";
import type {
  Assessment,
  Condition,
  Decision,
  FileHistory,
  Observations,
  Policy,
  Review,
} from "./contracts.js";

export function validatePolicy(value: unknown): Policy {
  const policy = object(value);
  if (
    Object.keys(policy).length !== 1 ||
    !Object.hasOwn(policy, "stale_change_review_days")
  )
    throw new EvaluationError("invalid policy keys");
  const days = integer(
    policy.stale_change_review_days,
    "stale_change_review_days",
  );
  if (days === 0)
    throw new EvaluationError("stale_change_review_days must be positive");
  return { stale_change_review_days: days };
}
export interface FileAge extends FileHistory {
  age_seconds: number | null;
  status: "new" | "stale" | "within_threshold";
}
export function changeAges(facts: Observations, days: number): FileAge[] {
  const observed = timestamp(facts.observed_at, "observed_at");
  const history = facts.change_history;
  if (!history || sha(history.base_sha) !== sha(facts.pr?.base_sha))
    throw new EvaluationError("change_history base SHA mismatch");
  if (!Array.isArray(facts.files) || !Array.isArray(history.files))
    throw new EvaluationError("change_history and files must be lists");
  const expected = new Map(
    facts.files.map((file) => [
      string(file.path, "file.path"),
      fileHistoryPath(file),
    ]),
  );
  const indexed = new Map(
    history.files.map((record) => [
      string(record.path, "history.path"),
      record,
    ]),
  );
  if (
    expected.size !== facts.files.length ||
    indexed.size !== history.files.length ||
    expected.size !== indexed.size ||
    [...expected.keys()].some((path) => !indexed.has(path)) ||
    facts.files.length !== integer(facts.change?.changed_files, "changed_files")
  )
    throw new EvaluationError("change_history file set mismatch");
  return [...expected.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([path, previous]) => {
      const record = indexed.get(path)!;
      if (record.history_path !== previous)
        throw new EvaluationError("change_history path mismatch");
      if (previous === null) {
        if (record.last_commit_sha !== null || record.last_changed_at !== null)
          throw new EvaluationError("new file must have null prior history");
        return { ...record, age_seconds: null, status: "new" };
      }
      sha(record.last_commit_sha);
      const age =
        observed - timestamp(record.last_changed_at, "last_changed_at");
      if (age < 0n)
        throw new EvaluationError("last_changed_at is after observed_at");
      return {
        ...record,
        age_seconds: Number(age) / 1e6,
        status:
          age > BigInt(days) * 86400_000000n ? "stale" : "within_threshold",
      };
    });
}
export function conditionsDecision(conditions: Condition[]): Decision {
  return conditions.some((c) => c.status === "unknown")
    ? "INSUFFICIENT_DATA"
    : conditions.some((c) => c.status === "blocked")
      ? "HUMAN_REVIEW_REQUIRED"
      : "SHADOW_CONDITIONS_MET";
}
export function assess(facts: Observations, policy: Policy): Assessment {
  validatePolicy(policy);
  const result: Assessment = {
    decision: "INSUFFICIENT_DATA",
    conditions: [],
    observations: facts,
    policy,
  };
  const condition = (
    name: string,
    status: Condition["status"],
    detail: unknown,
  ) => result.conditions.push({ name, status, detail });
  try {
    if (facts.schema_version !== 3)
      throw new EvaluationError("unsupported facts schema");
    string(facts.observed_at, "observed_at");
    if (!Array.isArray(facts.collection_errors))
      throw new EvaluationError("collection_errors required");
    if (facts.collection_errors.length)
      throw new EvaluationError(
        "collection incomplete: " + facts.collection_errors.join(", "),
      );
    const head = sha(facts.pr?.head_sha);
    sha(facts.pr?.base_sha);
    condition(
      "freshness",
      boolean(facts.stable, "stable") ? "pass" : "unknown",
      "History review inputs must agree in both samples",
    );
    const ages = changeAges(facts, policy.stale_change_review_days);
    const stale = ages.some((file) => file.status === "stale");
    const humanReviews: number[] = [];
    if (stale) {
      if (!Array.isArray(facts.reviews))
        throw new EvaluationError("reviews required");
      const latest = new Map<string, Review>();
      const published = facts.reviews.filter((r) => r.state !== "PENDING");
      for (const r of published) {
        string(r.submitted_at, "review.submitted_at");
        integer(r.id, "review.id");
      }
      published.sort((a, b) =>
        a.submitted_at! < b.submitted_at!
          ? -1
          : a.submitted_at! > b.submitted_at!
            ? 1
            : a.id - b.id,
      );
      for (const review of published) {
        if (
          !["APPROVED", "CHANGES_REQUESTED", "DISMISSED", "COMMENTED"].includes(
            review.state,
          )
        )
          throw new EvaluationError("unknown review state");
        if (review.state !== "COMMENTED")
          latest.set(string(review.author, "review.author"), review);
      }
      for (const review of latest.values()) {
        if (review.state !== "APPROVED" || review.commit_sha !== head) continue;
        const actor = string(review.author_type, "review.author_type");
        const association = string(
          review.author_association,
          "review.author_association",
        );
        if (
          actor === "User" &&
          ["OWNER", "MEMBER", "COLLABORATOR"].includes(association)
        )
          humanReviews.push(review.id);
      }
    }
    condition(
      "stale_change_review",
      stale && !humanReviews.length ? "blocked" : "pass",
      {
        threshold_days: policy.stale_change_review_days,
        observed_at: facts.observed_at,
        base_sha: facts.pr!.base_sha,
        files: ages,
        required_human_approvals: Number(stale),
        human_approval_review_ids: humanReviews.sort((a, b) => a - b),
      },
    );
    for (const key of ["additions", "deletions", "changed_files"] as const)
      integer(facts.change?.[key], key);
    result.decision = conditionsDecision(result.conditions);
  } catch (error) {
    condition(
      "data_integrity",
      "unknown",
      error instanceof Error ? error.message : String(error),
    );
  }
  return result;
}
