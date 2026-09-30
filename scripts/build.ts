/** 配布準備用 artifact を生成する。追跡済み dist/ は上書きしない。 */
import { build } from "esbuild";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile, rm } from "node:fs/promises";
const outdir = "build/dist";
await rm(outdir, { recursive: true, force: true });
await mkdir(outdir, { recursive: true });
await build({
  entryPoints: { index: "src/main.ts", cli: "src/cli.ts" },
  outdir,
  bundle: true,
  platform: "node",
  target: "node24",
  format: "esm",
  legalComments: "inline",
  charset: "utf8",
});
await writeFile(
  `${outdir}/THIRD_PARTY_LICENSES.txt`,
  await readFile("node_modules/smol-toml/LICENSE", "utf8"),
);
await writeFile(`${outdir}/package.json`, '{"type":"module"}\n');
const files = [
  "THIRD_PARTY_LICENSES.txt",
  "cli.js",
  "index.js",
  "package.json",
];
const sums = await Promise.all(
  files.map(
    async (file) =>
      `${createHash("sha256")
        .update(await readFile(`${outdir}/${file}`))
        .digest("hex")}  ${file}\n`,
  ),
);
await writeFile(`${outdir}/SHA256SUMS`, sums.join(""));
