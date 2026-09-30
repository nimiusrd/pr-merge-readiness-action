/** 配布 artifact の構成・symlink・checksum を検証する。 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { lstat, readFile, readdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";
export const DISTRIBUTION_FILES = [
  "SHA256SUMS",
  "THIRD_PARTY_LICENSES.txt",
  "cli.js",
  "index.js",
  "package.json",
];
export async function checkDistribution(directory = "dist"): Promise<void> {
  assert.deepEqual((await readdir(directory)).sort(), DISTRIBUTION_FILES);
  for (const file of DISTRIBUTION_FILES)
    assert.ok(
      (await lstat(join(directory, file))).isFile(),
      `${file}: regular file required`,
    );
  assert.deepEqual(
    JSON.parse(await readFile(join(directory, "package.json"), "utf8")),
    { type: "module" },
  );
  const files = DISTRIBUTION_FILES.filter((file) => file !== "SHA256SUMS");
  const hashes = await Promise.all(
    files.map(
      async (file) =>
        `${createHash("sha256")
          .update(await readFile(join(directory, file)))
          .digest("hex")}  ${file}\n`,
    ),
  );
  assert.equal(
    await readFile(join(directory, "SHA256SUMS"), "utf8"),
    hashes.join(""),
    "distribution checksum mismatch",
  );
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  await checkDistribution(process.argv[2]);
  console.log("Distribution validation passed");
}
