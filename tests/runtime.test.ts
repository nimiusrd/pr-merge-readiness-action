import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile, rm, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  actionMain,
  manualPrNumber,
  output,
  trustedConfig,
  verifySource,
} from "../src/runtime.js";
import { ACTION_REPOSITORY } from "../src/config.js";
import { DECISION_LABELS } from "../src/publish.js";
import { object } from "../src/contracts.js";
import {
  ACTION_SHA,
  BASE,
  HEAD,
  OLD,
  FixtureReader,
  FixtureWriter,
  prEvent,
  pull,
} from "./support.js";
async function context() {
  const root = await mkdtemp(join(tmpdir(), "pmr-runtime-"));
  const reader = new FixtureReader(),
    writer = new FixtureWriter();
  await writeFile(
    join(root, "event.json"),
    JSON.stringify({ inputs: { "pr-number": "1" } }),
  );
  const env = {
    INPUT_TOKEN: "test-token",
    GITHUB_REPOSITORY: reader.repository,
    GITHUB_EVENT_NAME: "workflow_dispatch",
    GITHUB_EVENT_PATH: join(root, "event.json"),
    GITHUB_SHA: HEAD,
    GITHUB_OUTPUT: join(root, "outputs"),
    GITHUB_STEP_SUMMARY: join(root, "summary"),
    GITHUB_ACTION_REF: ACTION_SHA,
    GITHUB_ACTION_REPOSITORY: ACTION_REPOSITORY,
  };
  return {
    root,
    reader,
    writer,
    env,
    close: () => rm(root, { recursive: true, force: true }),
  };
}
test("設定取得は default branch を一度だけ解決し以降 SHA に固定する", async () => {
  const api = new FixtureReader();
  const [config, pinned] = await trustedConfig(api, ".github/a&b.toml");
  assert.equal(pinned, BASE);
  assert.equal(api.requests.length, 3);
  assert.ok(api.requests[2]!.endsWith(`a%26b.toml?ref=${BASE}`));
  api.requests = [];
  assert.deepEqual(await trustedConfig(api, ".github/a&b.toml", pinned), [
    config,
    pinned,
  ]);
  assert.equal(api.requests.length, 1);
});
for (const reference of ["", "main", "v1", HEAD.slice(1), HEAD.toUpperCase()])
  test(`remote Action の SHA 固定を検証: ${reference}`, () => {
    assert.throws(() =>
      verifySource({
        GITHUB_ACTION_REF: reference,
        GITHUB_ACTION_REPOSITORY: ACTION_REPOSITORY,
      }),
    );
  });
test("提供元は runner の環境変数で検証する", () => {
  verifySource({
    GITHUB_ACTION_REF: HEAD,
    GITHUB_ACTION_REPOSITORY: ACTION_REPOSITORY,
  });
  assert.throws(() =>
    verifySource({
      GITHUB_ACTION_REF: HEAD,
      GITHUB_ACTION_REPOSITORY: "other/action",
    }),
  );
});
test("出力値から追加フィールドを注入できない", async () => {
  const c = await context();
  try {
    await assert.rejects(output({ safe: "value\ninjected=true" }, c.env));
    await assert.rejects(readFile(c.env.GITHUB_OUTPUT));
  } finally {
    await c.close();
  }
});
test("単一 PR の手動観測は設定・Summary・ラベルを一度の実行で更新する", async () => {
  const c = await context();
  try {
    assert.equal(await actionMain(c), 0);
    assert.deepEqual(c.writer.names(), [DECISION_LABELS.SHADOW_CONDITIONS_MET]);
    assert.equal(
      await readFile(c.env.GITHUB_OUTPUT, "utf8"),
      `operation=observe\nconfig-sha=${BASE}\n`,
    );
    assert.ok(
      (await readFile(c.env.GITHUB_STEP_SUMMARY, "utf8")).includes(
        "SHADOW_CONDITIONS_MET",
      ),
    );
    assert.deepEqual((await readdir(c.root)).sort(), [
      "event.json",
      "outputs",
      "summary",
    ]);
    assert.ok(
      !c.writer.calls.some(([, path]) => path.includes("state=closed")),
    );
  } finally {
    await c.close();
  }
});
for (const action of [
  "opened",
  "reopened",
  "synchronize",
  "edited",
  "ready_for_review",
  "converted_to_draft",
  "closed",
])
  test(`PR ${action} は提案を検証し default policy で判定する`, async () => {
    const c = await context();
    try {
      c.env.GITHUB_EVENT_NAME = "pull_request";
      const event = prEvent(action);
      if (action === "edited") event.changes = { base: {} };
      if (action === "closed")
        c.writer.pulls.set(
          1,
          pull([DECISION_LABELS.HUMAN_REVIEW_REQUIRED], "closed"),
        );
      c.reader.proposal = c.reader.proposal.replace("= 30", "= 1");
      await writeFile(c.env.GITHUB_EVENT_PATH, JSON.stringify(event));
      assert.equal(await actionMain(c), 0);
      assert.ok(c.reader.requests[0]!.endsWith(`?ref=${HEAD}`));
      assert.deepEqual(
        c.writer.names(),
        action === "closed" ? [] : [DECISION_LABELS.SHADOW_CONDITIONS_MET],
      );
      assert.equal(
        c.reader.requests.filter((path) =>
          path.endsWith("/git/ref/heads/trunk"),
        ).length,
        1,
      );
      assert.ok(!c.reader.paths.some((path) => path.endsWith("/reviews")));
    } finally {
      await c.close();
    }
  });
for (const reason of [
  "title",
  "body",
  "fork",
  "dependabot",
  "deleted",
  "unhandled",
])
  test(`対象外 PR を API 前に省略: ${reason}`, async () => {
    const c = await context();
    try {
      const event = prEvent();
      const pr = object(event.pull_request);
      if (["title", "body"].includes(reason)) {
        event.action = "edited";
        event.changes = { [reason]: {} };
      }
      if (reason === "fork") object(pr.head).repo = { id: 2 };
      if (reason === "deleted") object(pr.head).repo = null;
      if (reason === "dependabot") object(pr.user).login = "dependabot[bot]";
      if (reason === "unhandled") event.action = "labeled";
      c.env.GITHUB_EVENT_NAME = "pull_request";
      await writeFile(c.env.GITHUB_EVENT_PATH, JSON.stringify(event));
      assert.equal(await actionMain(c), 0);
      assert.equal(c.reader.requests.length, 0);
      assert.equal(c.writer.calls.length, 0);
      assert.equal(
        await readFile(c.env.GITHUB_OUTPUT, "utf8"),
        "operation=skip\n",
      );
    } finally {
      await c.close();
    }
  });
for (const name of ["schedule", "workflow_run", "pull_request_review"])
  test(`他イベントは payload 読み込み前に省略: ${name}`, async () => {
    const c = await context();
    try {
      c.env.GITHUB_EVENT_NAME = name;
      await rm(c.env.GITHUB_EVENT_PATH);
      assert.equal(await actionMain(c), 0);
      assert.equal(c.reader.requests.length, 0);
      assert.equal(c.writer.calls.length, 0);
    } finally {
      await c.close();
    }
  });
test("push はイベント payload を読まず対象コミットの設定だけを検証する", async () => {
  const c = await context();
  try {
    c.env.GITHUB_EVENT_NAME = "push";
    await rm(c.env.GITHUB_EVENT_PATH);
    assert.equal(await actionMain(c), 0);
    assert.deepEqual(c.reader.requests, [
      `${c.reader.prefix}/contents/.github/pr-merge-readiness.toml?ref=${HEAD}`,
    ]);
    assert.equal(c.reader.paths.length, 0);
    assert.equal(c.writer.calls.length, 0);
    assert.equal(
      await readFile(c.env.GITHUB_OUTPUT, "utf8"),
      `operation=validate-config\nconfig-sha=${HEAD}\n`,
    );
  } finally {
    await c.close();
  }
});
for (const eventName of ["pull_request", "push"])
  test(`不正提案なら default 設定も観測も公開も行わない: ${eventName}`, async () => {
    const c = await context();
    try {
      c.env.GITHUB_EVENT_NAME = eventName;
      c.reader.proposal = "unknown = true\n" + c.reader.proposal;
      await writeFile(c.env.GITHUB_EVENT_PATH, JSON.stringify(prEvent()));
      assert.equal(await actionMain(c), 1);
      assert.equal(c.reader.requests.length, 1);
      assert.equal(c.reader.paths.length, 0);
      assert.equal(c.writer.calls.length, 0);
    } finally {
      await c.close();
    }
  });
for (const inputs of [
  { "pr-number": "01" },
  { "pr-number": "-1" },
  { "pr-number": 1 },
  { "pr-number": false },
  { "pr-number": null },
  { "update-labels": true },
  [],
  "wrong",
])
  test(`不正な手動入力を API 前に拒否: ${JSON.stringify(inputs)}`, async () => {
    const c = await context();
    try {
      await writeFile(c.env.GITHUB_EVENT_PATH, JSON.stringify({ inputs }));
      assert.equal(await actionMain(c), 1);
      assert.equal(c.reader.requests.length, 0);
      assert.equal(c.writer.calls.length, 0);
    } finally {
      await c.close();
    }
  });
for (const inputs of [undefined, null, {}, { "pr-number": "" }])
  test(`番号省略を全 PR 観測として扱う: ${JSON.stringify(inputs)}`, () => {
    assert.equal(manualPrNumber({ inputs }), undefined);
  });
for (const timing of ["queued", "during", "after"])
  test(`提案の head 変更で未検証の対象へラベルを付けない: ${timing}`, async () => {
    const c = await context();
    try {
      c.env.GITHUB_EVENT_NAME = "pull_request";
      await writeFile(c.env.GITHUB_EVENT_PATH, JSON.stringify(prEvent()));
      const current = pull([DECISION_LABELS.HUMAN_REVIEW_REQUIRED]);
      object(current.head).sha = OLD;
      c.writer.pulls.set(1, current);
      if (timing === "queued") c.reader.state.headRefOid = OLD;
      if (timing === "during") c.reader.drift.headRefOid = OLD;
      assert.equal(await actionMain(c), timing === "after" ? 0 : 1);
      assert.deepEqual(c.writer.names(), [
        DECISION_LABELS.HUMAN_REVIEW_REQUIRED,
      ]);
      assert.ok(c.writer.calls.every(([verb]) => verb === "GET"));
      if (timing === "queued") assert.equal(c.reader.paths.length, 0);
    } finally {
      await c.close();
    }
  });
test("取得失敗は再観測ラベルへ置き換えて失敗する", async () => {
  const c = await context();
  try {
    c.reader.failure = "HTTP 403";
    c.writer.pulls.set(1, pull([DECISION_LABELS.SHADOW_CONDITIONS_MET]));
    assert.equal(await actionMain(c), 1);
    assert.deepEqual(c.writer.names(), [DECISION_LABELS.INSUFFICIENT_DATA]);
  } finally {
    await c.close();
  }
});
test("全対象の観測を終えてから公開し、一つの公開失敗でも後続を更新する", async () => {
  const c = await context();
  try {
    await writeFile(c.env.GITHUB_EVENT_PATH, "{}");
    c.reader.openNumbers = [1, 2];
    c.writer.pulls.set(2, pull());
    const graphql = c.reader.graphql.bind(c.reader);
    c.reader.graphql = async (number) => {
      assert.equal(c.writer.calls.length, 0);
      return graphql(number);
    };
    c.writer.fail = (verb, path) =>
      verb === "POST" && path.includes("/issues/1/");
    assert.equal(await actionMain(c), 1);
    assert.deepEqual(c.writer.names(2), [
      DECISION_LABELS.SHADOW_CONDITIONS_MET,
    ]);
    assert.ok(
      (await readFile(c.env.GITHUB_STEP_SUMMARY, "utf8")).includes("PR #1"),
    );
  } finally {
    await c.close();
  }
});
test("open PR がなくても閉じた PR の管理ラベルを清掃する", async () => {
  const c = await context();
  try {
    await writeFile(c.env.GITHUB_EVENT_PATH, "{}");
    c.reader.openNumbers = [];
    c.writer.pulls.set(
      1,
      pull([DECISION_LABELS.HUMAN_REVIEW_REQUIRED, "bug"], "closed"),
    );
    c.writer.closed = [{ number: 1, pull_request: {} }];
    assert.equal(await actionMain(c), 0);
    assert.deepEqual(c.writer.names(), ["bug"]);
    assert.ok(
      (await readFile(c.env.GITHUB_STEP_SUMMARY, "utf8")).includes(
        "評価対象の open PR はありません",
      ),
    );
  } finally {
    await c.close();
  }
});
test("Summary 書き込み失敗と token 欠落は公開前に失敗する", async () => {
  for (const kind of ["summary", "token"]) {
    const c = await context();
    try {
      if (kind === "summary")
        c.env.GITHUB_STEP_SUMMARY = join(c.root, "missing", "summary");
      else c.env.INPUT_TOKEN = "";
      assert.equal(await actionMain(c), 1);
      assert.equal(c.writer.calls.length, 0);
      if (kind === "token") assert.equal(c.reader.requests.length, 0);
    } finally {
      await c.close();
    }
  }
});
