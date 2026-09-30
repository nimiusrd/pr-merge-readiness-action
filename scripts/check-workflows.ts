/** 通常の workflow、SHA 固定、権限、手動配布と公開の順序を検証する。 */
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { parseDocument } from "yaml";
import { ACTION_REPOSITORY, parseConfig } from "../src/config.js";
import { object } from "../src/contracts.js";
import type { JsonObject } from "../src/contracts.js";
export function parseYaml(text: string): JsonObject {
  const doc = parseDocument(text);
  assert.deepEqual(doc.errors, []);
  return object(doc.toJS());
}
async function load(path: string): Promise<JsonObject> {
  return parseYaml(await readFile(path, "utf8"));
}
function steps(job: unknown): JsonObject[] {
  const values = object(job).steps;
  assert.ok(Array.isArray(values));
  return values.map(object);
}
export function checkActionFlow(action: JsonObject): void {
  assert.deepEqual(Object.keys(object(action.inputs)).sort(), [
    "config-path",
    "token",
  ]);
  assert.deepEqual(Object.keys(object(action.outputs)).sort(), [
    "config-sha",
    "operation",
  ]);
  assert.deepEqual(action.runs, { using: "node24", main: "dist/index.js" });
  assert.equal(
    object(object(action.inputs).token).default,
    "${{ github.token }}",
  );
  for (const value of Object.values(object(action.outputs)))
    assert.ok(!Object.hasOwn(object(value), "value"));
}
export function checkRuntime(
  workflow: JsonObject,
  allowPlaceholder = false,
): void {
  assert.deepEqual(workflow.permissions, {});
  assert.ok(!Object.hasOwn(workflow, "concurrency"));
  const jobs = object(workflow.jobs);
  assert.deepEqual(Object.keys(jobs), ["readiness"]);
  const job = object(jobs.readiness);
  for (const key of ["if", "needs", "outputs", "uses"])
    assert.ok(!Object.hasOwn(job, key));
  assert.equal(job["runs-on"], "ubuntu-26.04");
  assert.equal(job["timeout-minutes"], 45);
  assert.deepEqual(job.concurrency, {
    group: "autonomous-merge-check-writer",
    "cancel-in-progress": false,
    queue: "max",
  });
  assert.deepEqual(job.permissions, {
    contents: "read",
    "pull-requests": "write",
    issues: "write",
  });
  const manual = object(object(object(workflow.on).workflow_dispatch).inputs);
  assert.deepEqual(Object.keys(manual), ["pr-number"]);
  const calls = steps(job);
  assert.equal(calls.length, 1);
  assert.deepEqual(Object.keys(calls[0]!), ["uses"]);
  const reference = calls[0]!.uses;
  if (!(
    allowPlaceholder &&
    reference === ACTION_REPOSITORY + "@<RELEASE_COMMIT_SHA>"
  ))
    assert.match(
      String(reference),
      new RegExp(`^${ACTION_REPOSITORY}@[0-9a-f]{40}$`),
    );
}
export async function checkWorkflows(): Promise<void> {
  const action = await load("action.yml");
  checkActionFlow(action);
  assert.equal((await readFile(".node-version", "utf8")).trim(), "24");
  const project = object(JSON.parse(await readFile("package.json", "utf8")));
  assert.equal(object(project.engines).node, ">=24 <25");
  const compiler = object(
    object(JSON.parse(await readFile("tsconfig.json", "utf8"))).compilerOptions,
  );
  assert.equal(compiler.strict, true);
  assert.equal(compiler.noUncheckedIndexedAccess, true);
  const lock = object(JSON.parse(await readFile("package-lock.json", "utf8")));
  const locked = object(object(lock.packages)[""]);
  assert.deepEqual(locked.dependencies, project.dependencies);
  assert.deepEqual(locked.devDependencies, project.devDependencies);
  const example = await load("examples/pr-merge-readiness.yml");
  checkRuntime(example, true);
  assert.deepEqual(Object.keys(object(example.on)).sort(), [
    "pull_request",
    "push",
    "workflow_dispatch",
  ]);
  assert.deepEqual(object(example.on).pull_request, {
    types: [
      "opened",
      "reopened",
      "synchronize",
      "edited",
      "ready_for_review",
      "converted_to_draft",
      "closed",
    ],
  });
  const snippets = [
    ...(await readFile("README.md", "utf8")).matchAll(
      /```yaml\n([\s\S]*?)\n```/g,
    ),
  ];
  assert.equal(snippets.length, 1);
  assert.deepEqual(parseYaml(snippets[0]![1]!), example);
  const workflows = [example];
  for (const filename of await readdir(".github/workflows")) {
    if (!filename.endsWith(".yml")) continue;
    const workflow = await load(`.github/workflows/${filename}`);
    workflows.push(workflow);
    if (filename === "pr-merge-readiness.yml") {
      checkRuntime(workflow);
      assert.deepEqual(workflow.on, example.on);
    }
  }
  let nodeSetups = 0;
  for (const workflow of workflows) {
    const events = object(workflow.on);
    assert.ok(!Object.hasOwn(events, "schedule"));
    assert.ok(!Object.hasOwn(events, "workflow_call"));
    for (const value of Object.values(object(workflow.jobs))) {
      const job = object(value);
      assert.ok(!Object.hasOwn(job, "uses"));
      for (const step of steps(job)) {
        const uses = String(step.uses ?? "");
        if (uses === ACTION_REPOSITORY + "@<RELEASE_COMMIT_SHA>")
          assert.equal(workflow, example);
        else if (uses) assert.match(uses, /^[\w./-]+@[0-9a-f]{40}$/);
        assert.ok(
          !uses.startsWith("astral-sh/setup-uv@") &&
            !uses.startsWith("actions/setup-python@"),
        );
        if (uses.startsWith(ACTION_REPOSITORY + "@"))
          assert.ok(
            Object.keys(object(step.with ?? {})).every((key) =>
              Object.hasOwn(object(action.inputs), key),
            ),
          );
        if (uses.startsWith("actions/setup-node@")) {
          nodeSetups++;
          assert.equal(object(step.with)["node-version-file"], ".node-version");
        }
      }
    }
  }
  assert.equal(nodeSetups, 3);
  const ci = await load(".github/workflows/ci.yml"),
    release = await load(".github/workflows/release.yml");
  const required = [
    "npm ci",
    "npm test",
    "npm run lint",
    "npm run format:check",
    "npm run typecheck",
    "npm run check:workflows",
  ];
  const ciJobs = object(ci.jobs),
    releaseJobs = object(release.jobs);
  for (const job of [ciJobs.test, releaseJobs.verify]) {
    const commands = steps(job).map((step) => step.run);
    for (const command of required)
      assert.ok(commands.includes(command), command);
  }
  const bundle = steps(ciJobs.bundle);
  assert.ok(
    bundle.findIndex(
      (s) => s.name === "Test committed distribution before building",
    ) < bundle.findIndex((s) => s.run === "npm run build"),
  );
  assert.ok(bundle.some((s) => s.run === "npm run test:bundle"));
  const upload = bundle.at(-1)!;
  assert.equal(
    upload.if,
    "github.event_name == 'push' && github.ref == 'refs/heads/main'",
  );
  assert.equal(object(upload.with).name, "action-bundle");
  assert.equal(object(upload.with).path, "build/dist/");
  assert.deepEqual(release.on, { push: { tags: ["v*"] } });
  assert.deepEqual(release.permissions, { contents: "read" });
  assert.deepEqual(Object.keys(releaseJobs).sort(), ["publish", "verify"]);
  assert.equal(object(releaseJobs.publish).needs, "verify");
  assert.deepEqual(object(releaseJobs.publish).permissions, {
    contents: "write",
  });
  assert.equal(
    steps(releaseJobs.publish).at(-1)!.run,
    "bash scripts/publish_release.sh",
  );
  for (const job of Object.values(releaseJobs)) {
    const commands = steps(job);
    assert.equal(object(commands[0]!.with)["fetch-depth"], 0);
    for (const step of commands) {
      assert.ok(!String(step.run ?? "").includes("npm run build"));
      if (Object.hasOwn(object(step.env ?? {}), "RELEASE_VERSION"))
        assert.equal(
          object(step.env).RELEASE_VERSION,
          "${{ github.ref_name }}",
        );
    }
  }
  const verify = steps(releaseJobs.verify);
  assert.ok(
    verify.some((step) => step.run === "bash scripts/validate_release.sh"),
  );
  assert.ok(
    verify.some(
      (step) => object(step.env ?? {}).PMR_TEST_BUNDLE === "dist/index.js",
    ),
  );
  for (const filename of await readdir("examples"))
    if (filename.endsWith(".toml"))
      parseConfig(await readFile(`examples/${filename}`, "utf8"));
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  await checkWorkflows();
  console.log("Workflow and Action validation passed");
}
