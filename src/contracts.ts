/** 観測・判定の型と、境界での厳密な検証。 */
export type JsonObject = Record<string, unknown>;
export type Decision =
  "SHADOW_CONDITIONS_MET" | "HUMAN_REVIEW_REQUIRED" | "INSUFFICIENT_DATA";
export interface Policy {
  stale_change_review_days: number;
}
export interface Config {
  version: 4;
  review: Policy;
}
export interface PullRequest {
  number: number;
  state: string;
  head_sha: string;
  base_sha: string;
  base_ref?: string;
  updated_at?: string;
  additions?: number;
  deletions?: number;
  changed_files?: number;
}
export interface Review {
  id: number;
  author: string | null;
  state: string;
  commit_sha: string | null;
  submitted_at: string | null;
  author_type?: string | null;
  author_association?: string | null;
}
export interface ChangedFile {
  path: string;
  previous_path?: string | null;
  changeType: string;
  additions: number;
  deletions: number;
}
export interface FileHistory {
  path: string;
  history_path: string | null;
  last_commit_sha: string | null;
  last_changed_at: string | null;
}
export interface ChangeHistory {
  base_sha: string;
  files: FileHistory[];
}
export interface ObservationChange {
  group: string;
  identity: JsonObject | null;
  field: string;
  before: unknown;
  after: unknown;
}
export interface Observations {
  schema_version: 3;
  observed_at: string;
  repository: string;
  collection_errors: string[];
  stable: boolean;
  pr?: PullRequest;
  change?: {
    additions: number;
    deletions: number;
    changed_files: number;
    types?: Record<string, number>;
    binary_files?: number | null;
    mode_changes?: number | null;
  };
  files?: ChangedFile[];
  change_history?: ChangeHistory;
  reviews?: Review[];
  rechecked?: Record<string, boolean>;
  observation_changes?: ObservationChange[] | null;
}
export interface Condition {
  name: string;
  status: "pass" | "blocked" | "unknown";
  detail: unknown;
}
export interface Assessment {
  decision: Decision;
  conditions: Condition[];
  observations: Observations;
  policy: Policy;
}
export class EvaluationError extends Error {}
export function object(value: unknown): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    throw new EvaluationError("table required");
  return value as JsonObject;
}
export function integer(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0)
    throw new EvaluationError(`${name}: non-negative integer required`);
  return value;
}
export function string(value: unknown, name: string): string {
  if (typeof value !== "string" || !value)
    throw new EvaluationError(`${name}: non-empty string required`);
  return value;
}
export function boolean(value: unknown, name: string): boolean {
  if (typeof value !== "boolean")
    throw new EvaluationError(`${name}: boolean required`);
  return value;
}
export function sha(value: unknown): string {
  if (typeof value !== "string" || !/^[0-9a-f]{40}$/.test(value))
    throw new EvaluationError("invalid commit SHA");
  return value;
}
/** マイクロ秒を保持する。Date の丸めで閾値直後を見落とさない。 */
export function timestamp(value: unknown, name: string): bigint {
  const text = string(value, name);
  const match =
    /^(\d{4}-\d{2}-\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$/.exec(
      text,
    );
  if (!match)
    throw new EvaluationError(
      `${name}: timezone required or invalid timestamp`,
    );
  const [, date, hour, minute, second, fraction, offset] = match;
  const day = new Date(`${date}T00:00:00Z`);
  if (
    !Number.isFinite(day.getTime()) ||
    day.toISOString().slice(0, 10) !== date ||
    Number(hour) > 23 ||
    Number(minute) > 59 ||
    Number(second) > 59 ||
    (offset !== "Z" &&
      (Number(offset?.slice(1, 3)) > 23 || Number(offset?.slice(4, 6)) > 59))
  )
    throw new EvaluationError(`${name}: invalid timestamp`);
  const millis = Date.parse(`${date}T${hour}:${minute}:${second}${offset}`);
  if (!Number.isFinite(millis))
    throw new EvaluationError(`${name}: invalid timestamp`);
  return (
    BigInt(millis) * 1000n + BigInt((fraction ?? "").padEnd(6, "0").slice(0, 6))
  );
}
export function fileHistoryPath(file: ChangedFile): string | null {
  const path = string(file.path, "file.path");
  if (["ADDED", "COPIED"].includes(file.changeType)) return null;
  if (file.changeType === "RENAMED")
    return string(file.previous_path, "file.previous_path");
  if (["MODIFIED", "DELETED", "CHANGED", "UNCHANGED"].includes(file.changeType))
    return path;
  throw new EvaluationError("unknown file changeType");
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
