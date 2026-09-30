import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  checkActionFlow,
  checkRuntime,
  checkWorkflows,
  parseYaml,
} from "../scripts/check-workflows.js";
test("Action と全 workflow の契約を検証する", async () => {
  await checkWorkflows();
});
test("composite の定義と重複した出力式を拒否する", async () => {
  const action = parseYaml(await readFile("action.yml", "utf8"));
  assert.throws(() =>
    checkActionFlow({ ...action, runs: { using: "composite", steps: [] } }),
  );
  const outputs = structuredClone(action.outputs) as Record<
    string,
    Record<string, unknown>
  >;
  outputs.operation!.value = "${{ steps.run.outputs.operation }}";
  assert.throws(() => checkActionFlow({ ...action, outputs }));
});
test("利用側でタグ参照や再利用 workflow を許可しない", async () => {
  const workflow = parseYaml(
    await readFile("examples/pr-merge-readiness.yml", "utf8"),
  );
  const bad = structuredClone(workflow);
  (bad.jobs as Record<string, Record<string, unknown>>).readiness!.steps = [
    { uses: "nimiusrd/pr-merge-readiness-action@v1" },
  ];
  assert.throws(() => checkRuntime(bad));
  (bad.jobs as Record<string, Record<string, unknown>>).readiness!.uses =
    "some/workflow";
  assert.throws(() => checkRuntime(bad));
});
