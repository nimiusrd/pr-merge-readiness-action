import { test } from "node:test";
import assert from "node:assert/strict";
import { PublishError } from "../src/api.js";
import {
  cleanupClosed,
  DECISION_LABELS,
  ensureLabels,
  LABEL_DESCRIPTIONS,
  MANAGED_LABELS,
  publishPr,
} from "../src/publish.js";
import { BASE, HEAD, OLD, FixtureWriter, pull, report } from "./support.js";
const READY = DECISION_LABELS.SHADOW_CONDITIONS_MET,
  REQUIRED = DECISION_LABELS.HUMAN_REVIEW_REQUIRED,
  UNKNOWN = DECISION_LABELS.INSUFFICIENT_DATA;
test("管理ラベルだけを追加→削除で更新し、再実行を冪等にする", async () => {
  const api = new FixtureWriter();
  api.pulls.set(1, pull([READY, "bug", "shadow/レビュー待ち"]));
  assert.equal(await publishPr(api, 1, report()), "updated");
  assert.deepEqual(api.names(), ["bug", REQUIRED]);
  assert.deepEqual(
    api.calls.map(([verb]) => verb),
    ["GET", "POST", "DELETE", "DELETE"],
  );
  assert.equal(await publishPr(api, 1, report()), "unchanged");
});
for (const state of ["closed", "fork", "dependabot", "deleted"])
  test(`対象外 PR では管理ラベルを除去: ${state}`, async () => {
    const api = new FixtureWriter();
    const pr = pull(["bug", ...MANAGED_LABELS]);
    if (state === "closed") pr.state = "closed";
    if (state === "fork") (pr.head as Record<string, unknown>).repo = { id: 2 };
    if (state === "deleted") (pr.head as Record<string, unknown>).repo = null;
    if (state === "dependabot") pr.user = { login: "dependabot[bot]" };
    api.pulls.set(1, pr);
    await publishPr(api, 1, report());
    assert.deepEqual(api.names(), ["bug"]);
    assert.ok(api.calls.every(([verb]) => ["GET", "DELETE"].includes(verb)));
  });
test("dependencies ラベルや他の bot は除外条件にしない", async () => {
  const api = new FixtureWriter();
  const pr = pull(["dependencies"]);
  pr.user = { login: "renovate[bot]" };
  api.pulls.set(1, pr);
  await publishPr(api, 1, report());
  assert.deepEqual(api.names(), ["dependencies", REQUIRED]);
});
for (const field of ["head_sha", "base_sha", "base_ref"] as const)
  test(`観測後の対象変化には再観測ラベルを使う: ${field}`, async () => {
    const api = new FixtureWriter();
    const data = report();
    data.observations.pr![field] = field === "base_ref" ? "other" : OLD;
    await publishPr(api, 1, data);
    assert.deepEqual(api.names(), [UNKNOWN]);
  });
test("公開前に提案の head が変われば既存ラベルを保って省略する", async () => {
  const api = new FixtureWriter();
  const pr = pull([REQUIRED]);
  (pr.head as Record<string, unknown>).sha = OLD;
  api.pulls.set(1, pr);
  assert.equal(await publishPr(api, 1, report(), HEAD), "skipped");
  assert.deepEqual(api.names(), [REQUIRED]);
  assert.equal(api.calls.length, 1);
});
for (const verb of ["POST", "DELETE"])
  test(`書き込み途中の失敗は次の実行で回復できる: ${verb}`, async () => {
    const api = new FixtureWriter();
    api.pulls.set(1, pull([READY, "bug"]));
    api.fail = (method) => method === verb;
    await assert.rejects(publishPr(api, 1, report()), PublishError);
    assert.ok(api.names().includes(READY));
    if (verb === "DELETE") assert.ok(api.names().includes(REQUIRED));
    api.fail = undefined;
    await publishPr(api, 1, report());
    assert.deepEqual(api.names().sort(), ["bug", REQUIRED].sort());
  });
test("閉じた PR 清掃は番号を重複排除し、issue と再 open PR を除外する", async () => {
  const api = new FixtureWriter();
  api.pulls = new Map([
    [1, pull([READY, "bug"], "closed")],
    [2, pull([REQUIRED])],
  ]);
  api.closed = [
    { number: 1, pull_request: {} },
    { number: 2, pull_request: {} },
    { number: 3 },
  ];
  await cleanupClosed(api);
  assert.deepEqual(api.names(1), ["bug"]);
  assert.deepEqual(api.names(2), [REQUIRED]);
  assert.equal(
    api.calls.filter(([, path]) => path.endsWith("/pulls/1")).length,
    1,
  );
});
test("定義は必要時だけ作成し、説明の更新と色の正規化を扱う", async () => {
  const api = new FixtureWriter();
  api.definitions.clear();
  await ensureLabels(api);
  await ensureLabels(api);
  assert.equal(api.calls.filter(([verb]) => verb === "POST").length, 3);
  api.calls = [];
  api.definitions.get(REQUIRED)!.description = "古い説明";
  await ensureLabels(api);
  assert.equal(
    api.definitions.get(REQUIRED)!.description,
    LABEL_DESCRIPTIONS[REQUIRED],
  );
  for (const definition of api.definitions.values())
    definition.color = String(definition.color).toLowerCase();
  await ensureLabels(api);
  assert.equal(api.calls.filter(([verb]) => verb === "PATCH").length, 1);
  assert.deepEqual(
    [...api.definitions.keys()].sort(),
    Object.values(DECISION_LABELS).sort(),
  );
});
test("ラベル作成の競合 HTTP 422 は再取得して解決する", async () => {
  const api = new FixtureWriter();
  api.definitions.delete(REQUIRED);
  const request = api.request.bind(api);
  api.request = async (path, body, method) => {
    if (path === api.prefix + "/labels" && body?.name === REQUIRED) {
      await request(path, body, method);
      throw new PublishError("HTTP 422");
    }
    return request(path, body, method);
  };
  await ensureLabels(api);
  assert.ok(api.definitions.has(REQUIRED));
});
test("ラベル削除の HTTP 404 は成功として扱う", async () => {
  const api = new FixtureWriter();
  api.pulls.set(1, pull([READY]));
  const request = api.request.bind(api);
  api.request = async (path, body, method) => {
    if (method === "DELETE") throw new PublishError("HTTP 404");
    return request(path, body, method);
  };
  assert.equal(await publishPr(api, 1, report()), "updated");
});
test("report の repository・番号・decision が不正なら書き込まない", async () => {
  for (const kind of ["repo", "number", "decision"]) {
    const api = new FixtureWriter();
    const data = report();
    if (kind === "repo") data.observations.repository = "other/repo";
    if (kind === "number") data.observations.pr!.number = 2;
    if (kind === "decision") data.decision = "UNKNOWN" as never;
    await assert.rejects(publishPr(api, 1, data));
    assert.ok(api.calls.every(([verb]) => verb === "GET"));
  }
});
test("閉じた PR は古い SHA の観測でも清掃できる", async () => {
  const api = new FixtureWriter();
  api.pulls.set(1, pull([READY], "closed"));
  const data = report();
  data.observations.pr!.head_sha = BASE;
  await publishPr(api, 1, data);
  assert.deepEqual(api.names(), []);
});
