import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  parseConfig,
  validateConfig,
  relativePath,
  positive,
} from "../src/config.js";
import { configText } from "./support.js";
for (const [name, days] of [
  ["minimal", 30],
  ["review-policy", 14],
] as const)
  test(`設定例 ${name}`, async () => {
    assert.deepEqual(
      parseConfig(await readFile(`examples/${name}.toml`, "utf8")),
      { version: 4, review: { stale_change_review_days: days } },
    );
  });
for (const value of [
  "0",
  "-1",
  "true",
  '"30"',
  "30.0",
  "3e1",
  "inf",
  "nan",
  "9007199254740992",
])
  test(`TOML の整数契約: ${value}`, () => {
    assert.throws(() => parseConfig(configText.replace("= 30", `= ${value}`)));
  });
for (const value of ["1", "2", "3", "5", "true", '"4"', "4.0"])
  test(`設定 version を拒否: ${value}`, () => {
    assert.throws(
      () => parseConfig(configText.replace("= 4", `= ${value}`)),
      /version 4 required/,
    );
  });
test("未知・削除済みの設定、重複キー、必須キー欠落を拒否する", () => {
  for (const key of ["publication", "action_ref", "ci", "typo"])
    assert.throws(
      () =>
        validateConfig({
          version: 4,
          review: { stale_change_review_days: 30 },
          [key]: true,
        }),
      /invalid keys/,
    );
  for (const text of [
    "version = 4\nversion = 4",
    "[review]\nstale_change_review_days = 30",
    "version = 4",
    "version=4\n[review]",
    configText + "minimum_approvals = 1",
  ])
    assert.throws(() => parseConfig(text));
});
for (const path of [
  "../config",
  "/config",
  "a/../config",
  "./config",
  "a\\config",
  "a\nconfig",
  ".",
  "a//config",
  "a/",
])
  test(`設定 path を拒否: ${JSON.stringify(path)}`, () => {
    assert.throws(() => relativePath(path));
  });
for (const value of [
  "01",
  "-1",
  "０１",
  "",
  1,
  false,
  null,
  "1.0",
  "9007199254740992",
])
  test(`PR 番号を拒否: ${String(value)}`, () => {
    assert.throws(() => positive(value));
  });
