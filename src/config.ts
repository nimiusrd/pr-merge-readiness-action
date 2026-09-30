/** 設定 version 4 の TOML を厳密に検証する。 */
import { readFile } from "node:fs/promises";
import { posix } from "node:path";
import { parse } from "smol-toml";
import { EvaluationError, integer, object, string } from "./contracts.js";
import type { Config, JsonObject, Policy } from "./contracts.js";
import { validatePolicy } from "./evaluate.js";
export const ACTION_REPOSITORY = "nimiusrd/pr-merge-readiness-action";
export const CONFIG_PATH = ".github/pr-merge-readiness.toml";
export function keys(
  value: unknown,
  required: string[],
  optional: string[] = [],
): JsonObject {
  const table = object(value);
  const missing = required.filter((key) => !Object.hasOwn(table, key)).sort();
  const unknown = Object.keys(table)
    .filter((key) => ![...required, ...optional].includes(key))
    .sort();
  if (missing.length || unknown.length)
    throw new EvaluationError(
      `invalid keys: missing=${JSON.stringify(missing)}, unknown=${JSON.stringify(unknown)}`,
    );
  return table;
}
export function relativePath(value: string): string {
  string(value, "path");
  if (
    posix.isAbsolute(value) ||
    value.split("/").includes("..") ||
    posix.normalize(value) !== value ||
    value.endsWith("/") ||
    value.includes("\\") ||
    Array.from(value).some((character) => character.codePointAt(0)! < 32) ||
    value === "."
  )
    throw new EvaluationError("normalized repository-relative path required");
  return value;
}
export function validateConfig(value: unknown): Config {
  const config = keys(value, ["version", "review"]);
  if (config.version !== 4)
    throw new EvaluationError(
      "config version 4 required; see docs/workflow.md for migration",
    );
  const policy = keys(config.review, ["stale_change_review_days"]);
  return { version: 4, review: validatePolicy(policy) };
}
export function parseConfig(text: string): Config {
  // 小数 30.0 と整数 30 を区別してから、正確に扱える整数へ正規化する。
  const parsed = parse(text, {
    integersAsBigInt: true,
    unsafeKeyBehaviour: "throw",
  });
  const config = keys(parsed, ["version", "review"]);
  if (config.version !== 4n)
    throw new EvaluationError(
      "config version 4 required; see docs/workflow.md for migration",
    );
  const review = keys(config.review, ["stale_change_review_days"]);
  if (typeof review.stale_change_review_days !== "bigint")
    throw new EvaluationError(
      "stale_change_review_days: non-negative integer required",
    );
  return validateConfig({
    version: 4,
    review: {
      stale_change_review_days: integer(
        Number(review.stale_change_review_days),
        "stale_change_review_days",
      ),
    },
  });
}
export async function loadConfig(path: string): Promise<Config> {
  return parseConfig(await readFile(path, "utf8"));
}
export function policyFrom(config: Config): Policy {
  return { ...config.review };
}
export function positive(value: unknown): number {
  if (typeof value !== "string" || !/^[1-9][0-9]*$/.test(value))
    throw new EvaluationError("positive decimal integer required");
  return integer(Number(value), "number");
}
