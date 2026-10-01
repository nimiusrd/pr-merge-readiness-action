/** Action の with 入力を厳密に検証する。 */
import { EvaluationError, integer } from "./contracts.js";
import type { Policy } from "./contracts.js";
export const ACTION_REPOSITORY = "nimiusrd/pr-merge-readiness-action";
export function positive(value: unknown): number {
  if (typeof value !== "string" || !/^[1-9][0-9]*$/.test(value))
    throw new EvaluationError("positive decimal integer required");
  return integer(Number(value), "number");
}
export function policyFromInputs(env: NodeJS.ProcessEnv = process.env): Policy {
  const value = env["INPUT_STALE-CHANGE-REVIEW-DAYS"];
  try {
    return {
      stale_change_review_days: positive(
        value === undefined ? "30" : value.trim(),
      ),
    };
  } catch {
    throw new EvaluationError(
      "stale-change-review-days: positive decimal safe integer required",
    );
  }
}
