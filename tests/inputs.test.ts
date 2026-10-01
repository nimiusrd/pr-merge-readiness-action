import { test } from "node:test";
import assert from "node:assert/strict";
import { policyFromInputs, positive } from "../src/inputs.js";
test("with の省略時は30日、指定時はその閾値を使う", () => {
  assert.deepEqual(policyFromInputs({}), { stale_change_review_days: 30 });
  for (const value of ["1", "14", "30", "9007199254740991", " 14 "])
    assert.deepEqual(
      policyFromInputs({ "INPUT_STALE-CHANGE-REVIEW-DAYS": value }),
      { stale_change_review_days: Number(value) },
    );
});
for (const value of [
  "",
  " ",
  "0",
  "01",
  "-1",
  "+1",
  "30.0",
  "3e1",
  "true",
  "NaN",
  "Infinity",
  "３０",
  "9007199254740992",
  "1\n2",
])
  test(`with の不正日数を拒否: ${JSON.stringify(value)}`, () => {
    assert.throws(
      () => policyFromInputs({ "INPUT_STALE-CHANGE-REVIEW-DAYS": value }),
      /stale-change-review-days/,
    );
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
