/** 入力検証・観測・Summary・ラベル更新を単一実行で完結する。 */
import { appendFile, readFile } from "node:fs/promises";
import { GitHub } from "./api.js";
import type { Reader, Writer, ApiOptions } from "./api.js";
import { ACTION_REPOSITORY, policyFromInputs, positive } from "./inputs.js";
import {
  errorMessage,
  EvaluationError,
  object,
  sha,
  string,
} from "./contracts.js";
import type { JsonObject } from "./contracts.js";
import { observe } from "./observe.js";
import {
  cleanupClosed,
  ensureLabels,
  isPublicationTarget,
  publishPr,
} from "./publish.js";
import { escapeHtml, markdown } from "./report.js";
export async function output(
  values: Record<string, unknown>,
  env: NodeJS.ProcessEnv = process.env,
): Promise<void> {
  // 先に全値を検証してから書き込む。
  const lines = Object.entries(values).map(([key, value]) => {
    const text = String(value);
    if (/[\r\n]/.test(text)) throw new EvaluationError("invalid output value");
    return `${key}=${text}\n`;
  });
  if (env.GITHUB_OUTPUT) await appendFile(env.GITHUB_OUTPUT, lines.join(""));
}
export async function summary(
  text: string,
  env: NodeJS.ProcessEnv = process.env,
): Promise<void> {
  if (env.GITHUB_STEP_SUMMARY)
    await appendFile(env.GITHUB_STEP_SUMMARY, text + "\n");
}
export async function reportError(
  error: unknown,
  env: NodeJS.ProcessEnv = process.env,
): Promise<void> {
  console.log(JSON.stringify({ error: errorMessage(error) }));
  try {
    await summary(
      "処理失敗: <code>" + escapeHtml(errorMessage(error)) + "</code>",
      env,
    );
  } catch (summaryError) {
    console.log(JSON.stringify({ summary_error: errorMessage(summaryError) }));
  }
}
export function verifySource(env: NodeJS.ProcessEnv = process.env): void {
  if (!env.GITHUB_ACTION_REF)
    throw new EvaluationError(
      "local Actions are unsupported; use the remote Action pinned to a full SHA",
    );
  sha(env.GITHUB_ACTION_REF);
  if (env.GITHUB_ACTION_REPOSITORY !== ACTION_REPOSITORY)
    throw new EvaluationError("Action source repository mismatch");
}
export function prEventOperation(event: JsonObject): "observe" | "skip" {
  if (!isPublicationTarget(object(event.pull_request))) return "skip";
  if (
    event.action === "edited" &&
    !Object.hasOwn(object(event.changes ?? {}), "base")
  )
    return "skip";
  return [
    "opened",
    "reopened",
    "synchronize",
    "edited",
    "ready_for_review",
    "converted_to_draft",
    "closed",
  ].includes(String(event.action))
    ? "observe"
    : "skip";
}
export function manualPrNumber(event: JsonObject): number | undefined {
  if (event.inputs == null) return undefined;
  const inputs = object(event.inputs);
  if (Object.keys(inputs).some((key) => key !== "pr-number"))
    throw new EvaluationError(
      "manual inputs only support pr-number; labels are always updated",
    );
  const number = Object.hasOwn(inputs, "pr-number") ? inputs["pr-number"] : "";
  return number === "" ? undefined : positive(number);
}
export interface RuntimeOptions {
  env?: NodeJS.ProcessEnv;
  reader?: Reader;
  writer?: Writer;
}
export async function runAction(options: RuntimeOptions = {}): Promise<number> {
  const env = options.env ?? process.env;
  verifySource(env);
  const eventName = string(env.GITHUB_EVENT_NAME, "GITHUB_EVENT_NAME");
  if (!["pull_request", "workflow_dispatch"].includes(eventName)) {
    await output({ operation: "skip" }, env);
    return 0;
  }
  const event = object(
    JSON.parse(
      await readFile(
        string(env.GITHUB_EVENT_PATH, "GITHUB_EVENT_PATH"),
        "utf8",
      ),
    ),
  );
  if (eventName === "pull_request" && prEventOperation(event) === "skip") {
    await output({ operation: "skip" }, env);
    return 0;
  }
  const number =
    eventName === "workflow_dispatch" ? manualPrNumber(event) : undefined;
  const expectedHead =
    eventName === "pull_request"
      ? sha(object(object(event.pull_request).head).sha)
      : undefined;
  const policy = policyFromInputs(env);
  const token = env.INPUT_TOKEN?.trim() ?? "";
  if (!token) throw new EvaluationError("token required");
  const repository = string(env.GITHUB_REPOSITORY, "GITHUB_REPOSITORY");
  const apiOptions: ApiOptions = {
    token,
    apiUrl: env.GITHUB_API_URL,
    graphqlUrl: env.GITHUB_GRAPHQL_URL,
  };
  const api = options.reader ?? new GitHub(repository, apiOptions);
  await output({ operation: "observe" }, env);
  const reports = await observe(api, policy, event, number, expectedHead);
  await summary(
    reports.map(([, report]) => markdown(report)).join("\n") ||
      "評価対象の open PR はありません。",
    env,
  );
  const writer = options.writer ?? new GitHub(repository, apiOptions, true);
  let failed = reports.some(
    ([, report]) => report.decision === "INSUFFICIENT_DATA",
  );
  if (reports.length) await ensureLabels(writer);
  for (const [target, report] of reports) {
    try {
      console.log(
        JSON.stringify({
          pr: target,
          publication: await publishPr(writer, target, report, expectedHead),
        }),
      );
    } catch (error) {
      failed = true;
      await reportError(
        new EvaluationError(`PR #${target}: ${errorMessage(error)}`),
        env,
      );
    }
  }
  if (eventName === "workflow_dispatch" && number === undefined)
    await cleanupClosed(writer);
  return Number(failed);
}
export async function actionMain(
  options: RuntimeOptions = {},
): Promise<number> {
  try {
    return await runAction(options);
  } catch (error) {
    await reportError(error, options.env);
    return 1;
  }
}
