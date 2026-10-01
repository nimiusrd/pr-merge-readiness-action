/** ローカル Git remote と合成配布物で、公開の境界を検証する。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createHash } from "node:crypto";
import {
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { checkDistribution } from "../scripts/check-dist.js";
const exec = promisify(execFile);
const files = ["index.js", "package.json"];
async function checksums(directory: string) {
  const hashes = await Promise.all(
    files.map(
      async (file) =>
        `${createHash("sha256")
          .update(await readFile(join(directory, file)))
          .digest("hex")}  ${file}\n`,
    ),
  );
  await writeFile(join(directory, "SHA256SUMS"), hashes.join(""));
}
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "pmr-release-"));
  const repo = join(root, "repo"),
    remote = join(root, "remote.git");
  const git = (...args: string[]) =>
    exec("git", ["-C", repo, ...args], {
      env: {
        ...process.env,
        GIT_CONFIG_GLOBAL: "/dev/null",
        GIT_CONFIG_NOSYSTEM: "1",
      },
    });
  await mkdir(repo);
  await exec("git", ["init", "--bare", remote]);
  await git("init", "-b", "main");
  await git("config", "user.name", "Fixture");
  await git("config", "user.email", "fixture@example.invalid");
  await git("config", "commit.gpgsign", "false");
  await git("config", "tag.gpgsign", "false");
  const dist = join(repo, "dist");
  await mkdir(dist);
  await mkdir(join(repo, "scripts"));
  for (const file of files)
    await writeFile(
      join(dist, file),
      file === "package.json"
        ? '{"type":"module"}\n'
        : "synthetic distribution\n",
    );
  await checksums(dist);
  for (const file of ["validate_release.sh", "publish_release.sh"])
    await copyFile(resolve("scripts", file), join(repo, "scripts", file));
  const commit = async () => {
    await git("add", ".");
    await git("commit", "-m", "fixture");
    await git("push", "origin", "main");
    await git("tag", "-f", "v1.2.3");
    await git("push", "--force", "origin", "refs/tags/v1.2.3");
  };
  await git("remote", "add", "origin", remote);
  await commit();
  const sha = (await git("rev-parse", "HEAD")).stdout.trim();
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    RELEASE_VERSION: "v1.2.3",
    GITHUB_REF: "refs/tags/v1.2.3",
    GITHUB_SHA: sha,
  };
  const verify = () =>
    exec("bash", ["scripts/validate_release.sh"], { cwd: repo, env });
  return {
    root,
    repo,
    dist,
    git,
    env,
    verify,
    commit,
    close: () => rm(root, { recursive: true, force: true }),
  };
}
test("main にマージ済みの軽量タグと正しい配布物を受け付ける", async () => {
  const c = await fixture();
  try {
    await c.verify();
    await checkDistribution(c.dist);
  } finally {
    await c.close();
  }
});
test("注釈付きタグもコミット SHA で照合する", async () => {
  const c = await fixture();
  try {
    await c.git("tag", "-f", "-a", "v1.2.3", "-m", "annotated");
    await c.git("push", "--force", "origin", "refs/tags/v1.2.3");
    await c.verify();
  } finally {
    await c.close();
  }
});
for (const version of ["v01.2.3", "v1.2.3-rc.1", "v1.2.3+build", "main"])
  test(`公開タグの形式を拒否: ${version}`, async () => {
    const c = await fixture();
    try {
      c.env.RELEASE_VERSION = version;
      c.env.GITHUB_REF = `refs/tags/${version}`;
      await assert.rejects(c.verify());
    } finally {
      await c.close();
    }
  });
test("dirty checkout と別のイベント SHA を拒否する", async () => {
  const c = await fixture();
  try {
    c.env.GITHUB_SHA = "a".repeat(40);
    await assert.rejects(c.verify());
    c.env.GITHUB_SHA = (await c.git("rev-parse", "HEAD")).stdout.trim();
    await writeFile(join(c.repo, "untracked"), "dirty");
    await assert.rejects(c.verify());
  } finally {
    await c.close();
  }
});
for (const kind of ["checksum", "extra", "missing", "symlink"])
  test(`不正な配布物をタグ内で拒否: ${kind}`, async () => {
    const c = await fixture();
    try {
      if (kind === "checksum")
        await writeFile(join(c.dist, "index.js"), "tampered");
      if (kind === "extra") await writeFile(join(c.dist, "extra.js"), "extra");
      if (kind === "missing") await rm(join(c.dist, "package.json"));
      if (kind === "symlink") {
        await rm(join(c.dist, "package.json"));
        await symlink("index.js", join(c.dist, "package.json"));
      }
      await c.commit();
      c.env.GITHUB_SHA = (await c.git("rev-parse", "HEAD")).stdout.trim();
      await assert.rejects(c.verify());
      await assert.rejects(checkDistribution(c.dist));
    } finally {
      await c.close();
    }
  });
test("main に含まれないコミットのタグを拒否する", async () => {
  const c = await fixture();
  try {
    await c.git("checkout", "-b", "unmerged");
    await writeFile(join(c.repo, "change"), "new");
    await c.git("add", ".");
    await c.git("commit", "-m", "unmerged");
    await c.git("tag", "-f", "v1.2.3");
    await c.git("push", "--force", "origin", "refs/tags/v1.2.3");
    c.env.GITHUB_SHA = (await c.git("rev-parse", "HEAD")).stdout.trim();
    await assert.rejects(c.verify());
  } finally {
    await c.close();
  }
});
test("リモートタグの移動を検出する", async () => {
  const c = await fixture();
  try {
    const original = c.env.GITHUB_SHA!;
    await writeFile(join(c.repo, "change"), "new");
    await c.commit();
    await c.git("checkout", "--detach", original);
    await assert.rejects(c.verify());
  } finally {
    await c.close();
  }
});
test("公開スクリプトはタグ内の3ファイルだけを梱包し remote を変更しない", async () => {
  const c = await fixture();
  try {
    const stub = join(c.root, "bin");
    await mkdir(stub);
    const capture = join(c.root, "capture.tar.gz");
    await writeFile(
      join(stub, "gh"),
      '#!/usr/bin/env bash\nset -euo pipefail\n[[ "$1 $2" == "release create" ]]\ncp "${@: -1}" "$PMR_CAPTURE"\n',
      { mode: 0o755 },
    );
    const refs = (await c.git("ls-remote", "origin")).stdout;
    const summary = join(c.root, "summary.md");
    const sha = c.env.GITHUB_SHA;
    await exec("bash", ["scripts/publish_release.sh"], {
      cwd: c.repo,
      env: {
        ...c.env,
        PATH: stub + ":" + process.env.PATH,
        GITHUB_REPOSITORY: "example/project",
        GITHUB_SERVER_URL: "https://github.com",
        GITHUB_RUN_ID: "1",
        GITHUB_STEP_SUMMARY: summary,
        PMR_CAPTURE: capture,
      },
    });
    const summaryText = await readFile(summary, "utf8");
    assert.equal(typeof sha, "string");
    assert.match(summaryText, new RegExp("配布用コミット: `" + sha + "`"));
    assert.match(
      summaryText,
      new RegExp("固定参照: `example/project@" + sha + "`"),
    );
    assert.match(
      summaryText,
      /検証 run: https:\/\/github\.com\/example\/project\/actions\/runs\/1/,
    );
    assert.equal(summaryText.includes("配布ブランチ"), false);
    const listed = (await exec("tar", ["-tzf", capture])).stdout
      .trim()
      .split("\n")
      .sort();
    assert.deepEqual(listed, ["SHA256SUMS", ...files].sort());
    assert.equal((await c.git("ls-remote", "origin")).stdout, refs);
    assert.equal((await c.git("status", "--porcelain")).stdout, "");
  } finally {
    await c.close();
  }
});
