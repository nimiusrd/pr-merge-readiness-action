# 開発ルール

- 日本語で記述・回答する。
- コマンドは `devcontainer exec --workspace-folder . <command>` で Node.js 24 の Dev Container 内で実行する。`.node-version`・`package.json`・`package-lock.json` を正本にし、依存は npm で管理する。Git 操作はホスト、GitHub CLI はサンドボックス外で実行してよい。
- 提出前に `npm ci`、`npm test`、`npm run lint`、`npm run format:check`、`npm run typecheck`、`npm run check:workflows` を実行する。
- Action は `runs.using: node24` と `dist/index.js` で同梱 JavaScript バンドルを起動する。パッケージング・実行入口・CLI を変更した場合は `npm run build`、`npm run check:dist -- build/dist`、`npm run test:bundle` でも検証する。コマンドは Dev Container 内で実行する。
- `dist/` は main CI で検証済みのバンドルを使い、Cursor Automation が開く配布準備 PR で更新する。人がマージする。自動マージはしない。マージしたコミットへのタグ push で Release workflow がタグ内の配布物を検証・公開する。Release workflow は main・タグを更新せず、再ビルドしない。Automation は `gh release create` を実行しない。実装 PR にはローカルで生成した配布物を含めない。開発用出力先は `build/dist/` とする。利用側はリリースタグが指す40桁 SHA に固定する。公開手順は `docs/releases.md` の「Cursor Automation」を参照する。
- テストは Node.js の `node:test`・`node:assert/strict` で記述し、TypeScript を tsx で実行する。日時・通信・公開の境界を検証する。
- 旧システムの CLI・policy・artifact との互換処理は追加しない。
- 通常の workflow の step から Action を直接利用する。再利用可能 workflow（workflow_call / jobs.<id>.uses）と workflow 生成器は提供しない。
- 実測 JSON・API 応答・証跡を Git 管理しない。`evidence/` または Actions artifacts を使い、文書には run URL と検証要約を記載する。
- 自動マージ・自動承認・定期実行・必須 Check 登録を追加しない。

## Cursor Cloud specific instructions

- Docker・devcontainer CLI のない VM では Node.js 24・npm を直接用意し、`devcontainer exec --workspace-folder . <command>` を `<command>` に読み替える。
- `npm ci` で lock に固定した依存を導入する。提出前チェックとバンドル検証は上記と同じコマンドを使う。
- Action の設定は workflow の `with` に指定する。設定ファイル・設定検証 CLI は提供しない。
