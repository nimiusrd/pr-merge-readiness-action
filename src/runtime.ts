/** 設定検証・観測・Summary・ラベル更新を単一実行で完結する。 */
import { appendFile, readFile } from "node:fs/promises";
import { GitHub } from "./api.js";
import type { Reader, Writer, ApiOptions } from "./api.js";
import {
  ACTION_REPOSITORY,
  CONFIG_PATH,
  parseConfig,
  policyFrom,
  positive,
  relativePath,
} from "./config.js";
import {
  errorMessage,
  EvaluationError,
  object,
  sha,
  string,
} from "./contracts.js";
import type { Config, JsonObject } from "./contracts.js";
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
export async function trustedConfig(
  api: Reader,
  path: string,
  configSha?: string,
): Promise<[Config, string]> {
  relativePath(path);
  if (!configSha) {
    const branch = string(
      object(await api.request(api.prefix)).default_branch,
      "default_branch",
    );
    configSha = sha(
      object(
        object(
          await api.request(
            `${api.prefix}/git/ref/heads/${encodeURIComponent(branch)}`,
          ),
        ).object,
      ).sha,
    );
  }
  sha(configSha);
  const encoded = path.split("/").map(encodeURIComponent).join("/");
  const blob = object(
    await api.request(`${api.prefix}/contents/${encoded}?ref=${configSha}`),
  );
  if (blob.type !== "file" || blob.encoding !== "base64")
    throw new EvaluationError("config must be a regular TOML file");
  const content = string(blob.content, "config.content").replace(/\s/g, "");
  if (
    !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(
      content,
    )
  )
    throw new EvaluationError("invalid config base64");
  const text = new TextDecoder("utf-8", { fatal: true }).decode(
    Buffer.from(content, "base64"),
  );
  return [parseConfig(text), configSha];
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
  if (!["pull_request", "workflow_dispatch", "push"].includes(eventName)) {
    await output({ operation: "skip" }, env);
    return 0;
  }
  const event =
    eventName === "push"
      ? {}
      : object(
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
  const token = env.INPUT_TOKEN?.trim() ?? "";
  if (!token) throw new EvaluationError("token required");
  const repository = string(env.GITHUB_REPOSITORY, "GITHUB_REPOSITORY");
  const apiOptions: ApiOptions = {
    token,
    apiUrl: env.GITHUB_API_URL,
    graphqlUrl: env.GITHUB_GRAPHQL_URL,
  };
  const api = options.reader ?? new GitHub(repository, apiOptions);
  const path = env["INPUT_CONFIG-PATH"]?.trim() || CONFIG_PATH;
  if (eventName === "push") {
    const [, configSha] = await trustedConfig(api, path, sha(env.GITHUB_SHA));
    await output(
      { operation: "validate-config", "config-sha": configSha },
      env,
    );
    console.log(JSON.stringify({ valid: true, config_sha: configSha }));
    return 0;
  }
  if (expectedHead !== undefined) await trustedConfig(api, path, expectedHead);
  const [config, configSha] = await trustedConfig(api, path);
  await output({ operation: "observe", "config-sha": configSha }, env);
  await summary("設定コミット: <code>" + configSha + "</code>", env);
  const reports = await observe(
    api,
    policyFrom(config),
    event,
    number,
    expectedHead,
  );
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
