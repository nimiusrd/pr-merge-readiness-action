import { test } from "node:test";
import assert from "node:assert/strict";
import { assess, changeAges } from "../src/evaluate.js";
import { boolean, integer, sha, string, timestamp } from "../src/contracts.js";
import type { Observations } from "../src/contracts.js";
import { facts, policy, approval, HEAD, OLD } from "./support.js";

for (const [at, decision, status] of [
  ["2026-08-12T12:00:00.000001Z", "SHADOW_CONDITIONS_MET", "within_threshold"],
  ["2026-08-12T12:00:00.000000Z", "SHADOW_CONDITIONS_MET", "within_threshold"],
  ["2026-08-12T11:59:59.999999Z", "HUMAN_REVIEW_REQUIRED", "stale"],
] as const)
  test(`30 日の境界をマイクロ秒で判定: ${at}`, () => {
    const data = facts(at);
    assert.equal(assess(data, policy).decision, decision);
    assert.equal(changeAges(data, 30)[0]!.status, status);
  });
test("日時の timezone を瞬間として比較する", () => {
  assert.equal(
    assess(facts("2026-08-12T21:00:00+09:00"), policy).decision,
    "SHADOW_CONDITIONS_MET",
  );
});
for (const value of [0, -1, true, null, "30", 1.5, Number.MAX_SAFE_INTEGER + 1])
  test(`policy の不正値を拒否: ${String(value)}`, () => {
    assert.throws(() =>
      assess(facts(), { stale_change_review_days: value } as never),
    );
  });
for (const [overrides, expected] of [
  [{}, "SHADOW_CONDITIONS_MET"],
  [{ author_type: "Bot" }, "HUMAN_REVIEW_REQUIRED"],
  [{ author_association: "CONTRIBUTOR" }, "HUMAN_REVIEW_REQUIRED"],
  [{ commit_sha: OLD }, "HUMAN_REVIEW_REQUIRED"],
  [{ state: "COMMENTED" }, "HUMAN_REVIEW_REQUIRED"],
  [{ state: "PENDING", submitted_at: null }, "HUMAN_REVIEW_REQUIRED"],
  [{ state: "DISMISSED" }, "HUMAN_REVIEW_REQUIRED"],
  [{ author_type: undefined }, "INSUFFICIENT_DATA"],
  [{ author_association: null }, "INSUFFICIENT_DATA"],
  [{ state: "UNKNOWN" }, "INSUFFICIENT_DATA"],
] as const)
  test(`現在 head に対する人間の承認: ${JSON.stringify(overrides)}`, () => {
    const data = facts("2026-08-01T00:00:00Z");
    data.reviews = [approval(overrides)];
    assert.equal(assess(data, policy).decision, expected);
  });
test("同じ人の変更要求・dismiss は承認を取り消し、コメント・下書きは取り消さない", () => {
  for (const state of [
    "CHANGES_REQUESTED",
    "DISMISSED",
    "COMMENTED",
    "PENDING",
  ]) {
    const data = facts("2026-08-01T00:00:00Z");
    data.reviews = [
      approval({ id: 2 }),
      approval({ id: 1, state, submitted_at: "2026-09-11T13:00:00Z" }),
    ];
    assert.equal(
      assess(data, policy).decision,
      ["COMMENTED", "PENDING"].includes(state)
        ? "SHADOW_CONDITIONS_MET"
        : "HUMAN_REVIEW_REQUIRED",
    );
  }
});
test("他の人の変更要求は Ruleset に委ねる", () => {
  const data = facts("2026-08-01T00:00:00Z");
  data.reviews = [
    approval(),
    approval({ id: 2, author: "other", state: "CHANGES_REQUESTED" }),
  ];
  assert.equal(assess(data, policy).decision, "SHADOW_CONDITIONS_MET");
});
test("同時刻のレビューは id の順で判定する", () => {
  const data = facts("2026-08-01T00:00:00Z");
  data.reviews = [
    approval({ id: 2, state: "CHANGES_REQUESTED" }),
    approval({ id: 1 }),
  ];
  assert.equal(assess(data, policy).decision, "HUMAN_REVIEW_REQUIRED");
});
const mutations: [string, (data: Observations) => void][] = [
  [
    "history 欠落",
    (d) => {
      delete d.change_history;
    },
  ],
  [
    "files 欠落",
    (d) => {
      delete d.files;
    },
  ],
  [
    "base 不一致",
    (d) => {
      d.change_history!.base_sha = HEAD;
    },
  ],
  [
    "重複 file",
    (d) => {
      d.files!.push(d.files![0]!);
    },
  ],
  [
    "重複 history",
    (d) => {
      d.change_history!.files.push(d.change_history!.files[0]!);
    },
  ],
  [
    "history 欠落 file",
    (d) => {
      d.change_history!.files = [];
    },
  ],
  [
    "件数不一致",
    (d) => {
      d.change!.changed_files = 2;
    },
  ],
  [
    "path 不一致",
    (d) => {
      d.change_history!.files[0]!.history_path = "different";
    },
  ],
  [
    "SHA 不正",
    (d) => {
      d.change_history!.files[0]!.last_commit_sha = "invalid";
    },
  ],
  [
    "timezone 欠落",
    (d) => {
      d.change_history!.files[0]!.last_changed_at = "2026-08-01T00:00:00";
    },
  ],
  [
    "未来時刻",
    (d) => {
      d.change_history!.files[0]!.last_changed_at = "2027-01-01T00:00:00Z";
    },
  ],
  [
    "不正日時",
    (d) => {
      d.observed_at = "invalid";
    },
  ],
  [
    "不正 changeType",
    (d) => {
      d.files![0]!.changeType = "UNKNOWN";
    },
  ],
  [
    "不安定",
    (d) => {
      d.stable = false;
    },
  ],
  [
    "取得失敗",
    (d) => {
      d.collection_errors = ["HTTP 403"];
    },
  ],
  [
    "不正 schema",
    (d) => {
      (d as unknown as Record<string, unknown>).schema_version = true;
    },
  ],
];
for (const [name, mutate] of mutations)
  test(`情報不足を成功にしない: ${name}`, () => {
    const data = facts("2026-08-01T00:00:00Z");
    data.reviews = [approval()];
    mutate(data);
    assert.equal(assess(data, policy).decision, "INSUFFICIENT_DATA");
  });
test("rename は旧 path の履歴を引き継ぎ、新規ファイルは履歴を持たない", () => {
  const data = facts("2026-08-01T00:00:00Z");
  Object.assign(data.files![0]!, {
    path: "renamed.rs",
    previous_path: "whatever.rs",
    changeType: "RENAMED",
  });
  data.change_history!.files[0]!.path = "renamed.rs";
  assert.equal(assess(data, policy).decision, "HUMAN_REVIEW_REQUIRED");
  Object.assign(data.files![0]!, { changeType: "ADDED" });
  Object.assign(data.change_history!.files[0]!, {
    history_path: null,
    last_commit_sha: null,
    last_changed_at: null,
  });
  assert.equal(assess(data, policy).decision, "SHADOW_CONDITIONS_MET");
  data.change_history!.files[0]!.last_commit_sha = OLD;
  assert.equal(assess(data, policy).decision, "INSUFFICIENT_DATA");
});
test("共通境界は型変換せず不正値を拒否する", () => {
  for (const value of [null, true, -1, "0", [], NaN, Infinity])
    assert.throws(() => integer(value, "count"));
  for (const value of [null, 0, 1, "false", []])
    assert.throws(() => boolean(value, "stable"));
  for (const value of [null, "", 0, []])
    assert.throws(() => string(value, "name"));
  for (const value of [
    null,
    "",
    HEAD.slice(1),
    HEAD + "a",
    HEAD.toUpperCase(),
    "g".repeat(40),
  ])
    assert.throws(() => sha(value));
  for (const value of [
    "2026-02-30T00:00:00Z",
    "2026-01-01T24:00:00Z",
    "2026-01-01T00:00:00+24:00",
  ])
    assert.throws(() => timestamp(value, "at"));
});
