import { test } from "node:test";
import assert from "node:assert/strict";
import { CollectionError, ProposalHeadChanged } from "../src/api.js";
import { ChangeHistoryCollector, collect, targets } from "../src/collect.js";
import { assess } from "../src/evaluate.js";
import { markdown } from "../src/report.js";
import { observe } from "../src/observe.js";
import { AT, BASE, HEAD, OLD, FixtureReader, file, policy } from "./support.js";

test("patch・review 本文・commit message を保存せず必要な API だけを読む", async () => {
  const api = new FixtureReader();
  const data = await collect(api, 1, { policy, now: () => AT });
  assert.equal(assess(data, policy).decision, "SHADOW_CONDITIONS_MET");
  assert.equal(data.change!.binary_files, null);
  assert.equal(data.change!.mode_changes, null);
  assert.deepEqual(api.paths, ["/pulls/1/files"]);
  assert.ok(!JSON.stringify(data).includes("DO NOT SAVE"));
});
for (const drift of [
  { headRefOid: OLD },
  { baseRefOid: OLD },
  { baseRefName: "other" },
  { changedFiles: 2 },
  { additions: 9 },
  { deletions: 9 },
])
  test(`判定対象の変化を情報不足とする: ${JSON.stringify(drift)}`, async () => {
    const api = new FixtureReader();
    api.drift = drift;
    assert.equal(
      assess(await collect(api, 1, { policy, now: () => AT }), policy).decision,
      "INSUFFICIENT_DATA",
    );
  });
for (const drift of [
  { updatedAt: "2026-09-11T13:00:00Z" },
  { state: "CLOSED" },
  { isDraft: true },
  { mergeable: "UNKNOWN" },
  { reviewDecision: "CHANGES_REQUESTED" },
])
  test(`GitHub の表示状態だけでは判定を無効にしない: ${JSON.stringify(drift)}`, async () => {
    const api = new FixtureReader();
    api.drift = drift;
    assert.equal(
      assess(await collect(api, 1, { policy, now: () => AT }), policy).decision,
      "SHADOW_CONDITIONS_MET",
    );
  });
test("rename 元の path と base SHA を URL エンコードして固定する", async () => {
  const api = new FixtureReader();
  const previous = "古い dir/a&b?#.go";
  Object.assign(api.files[0]!, {
    filename: "new.go",
    status: "renamed",
    previous_filename: previous,
  });
  const data = await collect(api, 1, { policy, now: () => AT });
  const query = new URL(api.requests[0]!, "https://example.invalid")
    .searchParams;
  assert.equal(query.get("path"), previous);
  assert.equal(query.get("sha"), BASE);
  assert.equal(data.change_history!.files[0]!.history_path, previous);
  delete api.files[0]!.previous_filename;
  assert.equal(
    assess(await collect(api, 1, { policy, now: () => AT }), policy).decision,
    "INSUFFICIENT_DATA",
  );
});
for (const status of ["added", "copied", "modified", "removed"])
  test(`新規だけ履歴照会を省略する: ${status}`, async () => {
    const api = new FixtureReader();
    api.files[0]!.status = status;
    api.commits = [];
    const data = await collect(api, 1, { policy, now: () => AT });
    const isNew = ["added", "copied"].includes(status);
    assert.equal(
      assess(data, policy).decision,
      isNew ? "SHADOW_CONDITIONS_MET" : "INSUFFICIENT_DATA",
    );
    assert.equal(api.requests.length, isNew ? 0 : 1);
  });
for (const response of [
  [],
  {},
  [{}, {}],
  [{ sha: "invalid" }],
  [{ sha: OLD, commit: { committer: { date: null } } }],
])
  test(`欠落した履歴を情報不足とする: ${JSON.stringify(response)}`, async () => {
    const api = new FixtureReader();
    api.commits = response;
    assert.equal(
      assess(await collect(api, 1, { policy, now: () => AT }), policy).decision,
      "INSUFFICIENT_DATA",
    );
  });
test("重複ファイル、PR 件数・合計の不一致、ページ取得失敗を成功にしない", async () => {
  for (const mode of ["duplicate", "count", "additions", "failure"]) {
    const api = new FixtureReader();
    if (mode === "duplicate") api.files.push(api.files[0]!);
    if (mode === "count") api.state.changedFiles = 2;
    if (mode === "additions") api.state.additions = 7;
    if (mode === "failure") api.failure = "HTTP 403";
    const data = await collect(api, 1, { policy, now: () => AT });
    assert.ok(data.collection_errors.length);
    assert.equal(assess(data, policy).decision, "INSUFFICIENT_DATA");
  }
});
test("予算事前確認は残量を温存し、成功履歴を base と path で共有する", async () => {
  const api = new FixtureReader();
  const collector = new ChangeHistoryCollector(api, 100, 2);
  const first = await collector.collect(BASE, [file("one.go")]);
  await assert.rejects(
    collector.collect(BASE, [file("two.go"), file("three.go")]),
    /1\/2 used, 2 needed/,
  );
  assert.equal(api.requests.length, 1);
  first.files[0]!.last_commit_sha = HEAD;
  const renamed = await collector.collect(BASE, [
    { ...file("new.go", "RENAMED"), previous_path: "one.go" },
  ]);
  assert.equal(renamed.files[0]!.last_commit_sha, OLD);
  assert.equal(api.requests.length, 1);
  await collector.collect(HEAD, [file("one.go")]);
  assert.equal(api.requests.length, 2);
  await collector.collect(BASE, [file("new.go", "ADDED")]);
  assert.equal(api.requests.length, 2);
  await assert.rejects(collector.collect(BASE, [file("two.go")]), /2\/2 used/);
});
test("失敗した履歴要求も予算を消費し、新しい実行へは引き継がない", async () => {
  const api = new FixtureReader();
  api.commits = [];
  const collector = new ChangeHistoryCollector(api, 100, 1);
  await assert.rejects(collector.collect(BASE, [file()]));
  api.commits = undefined;
  await assert.rejects(collector.collect(BASE, [file()]), /1\/1 used/);
  await new ChangeHistoryCollector(api, 100, 1).collect(BASE, [file()]);
  assert.equal(api.requests.length, 2);
});
test("一つの PR の履歴件数にも上限を設ける", async () => {
  const api = new FixtureReader();
  await assert.rejects(
    new ChangeHistoryCollector(api, 0).collect(BASE, [file()]),
    /file limit/,
  );
  assert.equal(api.requests.length, 0);
});
test("観測中に閾値を跨いだら再観測を要求する", async () => {
  const api = new FixtureReader();
  api.lastChanged = "2026-08-12T12:00:00Z";
  let calls = 0;
  const data = await collect(api, 1, {
    policy,
    now: () => (++calls === 3 ? "2026-09-11T12:00:00.000001Z" : AT),
  });
  assert.ok(data.collection_errors[0]!.includes("threshold crossed"));
  assert.ok(!api.paths.some((path) => path.endsWith("/reviews")));
});
test("レビューの順序だけは無視し、作者種別・承認取消は再観測を要求する", async () => {
  for (const mode of ["order", "identity", "dismiss", "removed"]) {
    const api = new FixtureReader();
    api.lastChanged = "2026-08-01T00:00:00Z";
    api.reviews.push({ ...api.reviews[0], id: 2 });
    const pages = api.pages.bind(api);
    api.pages = async (path) => {
      const records = await pages(path);
      if (
        path.endsWith("/reviews") &&
        api.paths.filter((p) => p.endsWith("/reviews")).length === 2
      ) {
        if (mode === "order") records.reverse();
        if (mode === "identity")
          (records[0]!.user as Record<string, unknown>).type = "Bot";
        if (mode === "dismiss") records[0]!.state = "DISMISSED";
        if (mode === "removed") return [];
      }
      return records;
    };
    const data = await collect(api, 1, { policy, now: () => AT });
    assert.equal(data.stable, mode === "order");
    assert.ok(!JSON.stringify(data).includes("DO NOT SAVE"));
  }
});
for (const timing of ["before", "during"])
  test(`イベントの head が変われば観測自体を中断: ${timing}`, async () => {
    const api = new FixtureReader();
    if (timing === "before") api.state.headRefOid = OLD;
    else api.drift.headRefOid = OLD;
    await assert.rejects(
      collect(api, 1, { policy, now: () => AT, expectedHead: HEAD }),
      ProposalHeadChanged,
    );
    if (timing === "before") assert.equal(api.paths.length, 0);
  });
test("手動の全 PR 観測で実行全体の予算を共有する", async () => {
  const api = new FixtureReader();
  api.openNumbers = [1, 2];
  const reports = await observe(api, policy, {});
  assert.equal(reports.length, 2);
  assert.equal(api.requests.length, 1);
});
test("対象は指定番号、イベントの PR、現在 open PR の順に選ぶ", async () => {
  const api = new FixtureReader();
  assert.deepEqual(await targets(api, { pull_request: { number: 9 } }), [9]);
  assert.deepEqual(await targets(api, {}, 7), [7]);
  assert.deepEqual(await targets(api, {}), [1]);
  await assert.rejects(targets(api, {}, 0));
});
test("Summary は正規化メタデータを HTML エスケープする", async () => {
  const api = new FixtureReader();
  api.drift.baseRefName = "<script>alert(1)</script>";
  const text = markdown(
    assess(await collect(api, 1, { policy, now: () => AT }), policy),
  );
  assert.ok(text.includes("&lt;script&gt;"));
  assert.ok(!text.includes("<script>"));
  assert.ok(!text.includes("DO NOT SAVE"));
});
test("API 履歴の失敗を空の成功にしない", async () => {
  const api = new FixtureReader();
  api.request = async () => {
    throw new CollectionError("HTTP 403");
  };
  const data = await collect(api, 1, { policy, now: () => AT });
  assert.equal(assess(data, policy).decision, "INSUFFICIENT_DATA");
});
