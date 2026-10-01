/** バンドルを依存のない別ディレクトリで実行し、実際の HTTP 境界を検証する。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { once } from "node:events";
import { copyFile, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { ACTION_REPOSITORY } from "../src/inputs.js";
import { ACTION_SHA, BASE, HEAD, OLD, prEvent } from "./support.js";
const bundle = process.env.PMR_TEST_BUNDLE;
async function isolated() {
  const root = await mkdtemp(join(tmpdir(), "pmr-bundle-"));
  assert.ok(bundle);
  for (const name of ["index.js", "package.json"])
    await copyFile(join(dirname(resolve(bundle)), name), join(root, name));
  // 利用側が CommonJS のプロジェクトでも、配布物自身の module 宣言を使う。
  await writeFile(
    join(root, "consumer.cjs"),
    'throw new Error("must not load consumer code")',
  );
  return { root, close: () => rm(root, { recursive: true, force: true }) };
}
async function run(
  root: string,
  filename: string,
  args: string[],
  extra: NodeJS.ProcessEnv = {},
) {
  const child = spawn(process.execPath, [join(root, filename), ...args], {
    cwd: root,
    env: { PATH: "", ...extra },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "",
    stderr = "";
  child.stdout.on("data", (chunk) => {
    stdout += String(chunk);
  });
  child.stderr.on("data", (chunk) => {
    stderr += String(chunk);
  });
  const timer = setTimeout(() => child.kill(), 10_000);
  try {
    const [code] = await once(child, "close");
    return { code, stdout, stderr };
  } finally {
    clearTimeout(timer);
  }
}
test(
  "Action の remote source 検証は配布版でも動作する",
  { skip: !bundle },
  async () => {
    const c = await isolated();
    try {
      const result = await run(c.root, "index.js", [], {
        GITHUB_ACTION_REF: "main",
        GITHUB_ACTION_REPOSITORY: ACTION_REPOSITORY,
      });
      assert.equal(result.code, 1);
      assert.ok(result.stdout.includes("invalid commit SHA"));
    } finally {
      await c.close();
    }
  },
);
test(
  "対象外イベントは token・payload なしで省略する",
  { skip: !bundle },
  async () => {
    const c = await isolated();
    try {
      const destination = join(c.root, "outputs");
      const result = await run(c.root, "index.js", [], {
        GITHUB_ACTION_REF: ACTION_SHA,
        GITHUB_ACTION_REPOSITORY: ACTION_REPOSITORY,
        GITHUB_EVENT_NAME: "workflow_run",
        GITHUB_OUTPUT: destination,
      });
      assert.equal(result.code, 0, result.stderr);
      assert.equal(await readFile(destination, "utf8"), "operation=skip\n");
    } finally {
      await c.close();
    }
  },
);
for (const mode of ["push", "observe", "stale", "error", "skip", "drift"])
  test(`配布 Action の HTTP 結合検証: ${mode}`, { skip: !bundle }, async () => {
    const c = await isolated();
    const paths: string[] = [],
      writes: string[] = [];
    let reads = 0;
    const server = createServer(async (request, response) => {
      try {
        assert.equal(request.headers.authorization, "Bearer bundle-token");
        const path = request.url!;
        paths.push(path);
        if (request.method !== "GET" && path !== "/graphql") writes.push(path);
        let data: unknown;
        if (path === "/graphql") {
          reads++;
          let body = "";
          for await (const chunk of request) body += String(chunk);
          assert.ok(!JSON.parse(body).query.includes("mergeStateStatus"));
          data = {
            data: {
              repository: {
                pullRequest: {
                  number: 1,
                  state: "OPEN",
                  headRefOid: mode === "drift" && reads === 2 ? OLD : HEAD,
                  baseRefOid: BASE,
                  baseRefName: "main",
                  updatedAt: new Date().toISOString(),
                  additions: 3,
                  deletions: 1,
                  changedFiles: 1,
                },
              },
            },
          };
        } else if (
          path.includes("/contents/") ||
          path.endsWith("/git/ref/heads/main")
        ) {
          throw new Error("設定ファイルを取得してはいけない");
        } else if (path.includes("/files?"))
          data = [
            {
              filename: "one.go",
              status: "modified",
              additions: 3,
              deletions: 1,
              patch: "DO NOT SAVE PATCH",
            },
          ];
        else if (path.includes("/commits?"))
          data = [
            {
              sha: OLD,
              commit: {
                committer: {
                  date: new Date(
                    Date.now() - (mode === "stale" ? 15 : 10) * 86400000,
                  ).toISOString(),
                },
              },
            },
          ];
        else if (path.includes("/reviews?")) data = [];
        else if (path.endsWith("/pulls/1"))
          data = {
            state: "open",
            head: { sha: HEAD, repo: { id: 1 } },
            base: { sha: BASE, ref: "main", repo: { id: 1 } },
            user: { login: "contributor" },
            labels: [],
          };
        else if (path.includes("/labels/"))
          data = { color: "", description: "" };
        else if (path.endsWith("/labels") && request.method === "POST")
          data = [];
        else throw new Error(`unexpected HTTP ${path}`);
        response.setHeader("Content-Type", "application/json");
        response.end(JSON.stringify(data));
      } catch (error) {
        response.writeHead(500);
        response.end(String(error));
      }
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    try {
      const address = server.address();
      assert.ok(address && typeof address === "object");
      const url = `http://127.0.0.1:${address.port}`;
      const event = prEvent();
      if (mode === "skip") objectHead(event).repo = { id: 2 };
      const eventPath = join(c.root, "event.json");
      await writeFile(eventPath, JSON.stringify(event));
      const outputPath = join(c.root, "outputs"),
        summaryPath = join(c.root, "summary");
      const result = await run(c.root, "index.js", [], {
        INPUT_TOKEN: "bundle-token",
        "INPUT_STALE-CHANGE-REVIEW-DAYS": mode === "error" ? "0" : "14",
        GITHUB_ACTION_REF: ACTION_SHA,
        GITHUB_ACTION_REPOSITORY: ACTION_REPOSITORY,
        GITHUB_REPOSITORY: "example/project",
        GITHUB_EVENT_NAME: mode === "push" ? "push" : "pull_request",
        GITHUB_EVENT_PATH: eventPath,
        GITHUB_SHA: HEAD,
        GITHUB_API_URL: url,
        GITHUB_GRAPHQL_URL: url + "/graphql",
        GITHUB_OUTPUT: outputPath,
        GITHUB_STEP_SUMMARY: summaryPath,
      });
      assert.equal(
        result.code,
        ["error", "drift"].includes(mode) ? 1 : 0,
        result.stdout + result.stderr,
      );
      if (mode === "push") {
        assert.equal(paths.length, 0);
        assert.ok(
          (await readFile(outputPath, "utf8")).includes("operation=skip"),
        );
      } else if (mode === "skip") {
        assert.equal(paths.length, 0);
        assert.equal(await readFile(outputPath, "utf8"), "operation=skip\n");
      } else if (["error", "drift"].includes(mode))
        assert.equal(writes.length, 0);
      else {
        assert.ok(writes.some((path) => path.endsWith("/issues/1/labels")));
        const text = await readFile(summaryPath, "utf8");
        assert.ok(
          text.includes(
            mode === "stale"
              ? "HUMAN_REVIEW_REQUIRED"
              : "SHADOW_CONDITIONS_MET",
          ),
        );
        assert.ok(!text.includes("DO NOT SAVE"));
        assert.equal(
          paths.filter((path) => path.includes("/reviews?")).length,
          mode === "stale" ? 2 : 0,
        );
      }
    } finally {
      server.close();
      await once(server, "close");
      await c.close();
    }
  });
function objectHead(event: Record<string, unknown>): Record<string, unknown> {
  return (event.pull_request as { head: Record<string, unknown> }).head;
}
