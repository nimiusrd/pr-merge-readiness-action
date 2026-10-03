# リリース手順

公開担当者は、main CI の配布物を採用し、配布準備 PR を手動で作成します。PR を main にマージした後、そのコミットにタグを付けて push すると Release workflow が公開します。

| 工程                              | 完了条件                                                                 |
| --------------------------------- | ------------------------------------------------------------------------ |
| 1. ソースと CI を選ぶ             | 公開するソースの main CI 全体が成功している                              |
| 2. 配布準備 PR を作成・マージする | 検証済み artifact を `dist/` に配置し、PR の CI とレビューが完了している |
| 3. タグを push する               | マージ後の main CI が成功し、ソースと配布物の対応を確認している          |
| 4. 公開を確認する                 | タグの SHA・公開済み Immutable Release・asset が一致している             |
| 5. 参照更新 PR を作る             | 導入例と運用 workflow を公開済みの40桁 SHA に固定している                |

正本は [CI](.github/workflows/ci.yml)、[Release workflow](.github/workflows/release.yml)、[タグ・配布物の検証](scripts/validate_release.sh)、[公開スクリプト](scripts/publish_release.sh)です。Release workflow の手動実行や配布準備 PR の自動作成は行いません。

`RELEASE.md` の工程1から工程5を、起動メッセージの `PMR_VERSION` と `PMR_SOURCE_SHA` で実行する。コマンド、完了条件、失敗時の対応は `RELEASE.md` に従う。

`PMR_VERSION` は未使用の `vMAJOR.MINOR.PATCH`、`PMR_SOURCE_SHA` は工程1で選んだ main CI の40桁 SHA である。どちらかが無い場合は終了する。`package.json` の version、既存タグ、ラベルからは決めない。

- `devcontainer exec --workspace-folder .` は付けず、同じ引数を Node.js 24 で直接実行する。
- ブランチ名は実行環境が要求する接頭辞に合わせる。要求が無ければ配布準備は `prepare-$PMR_VERSION`、参照更新は `refs-$PMR_VERSION` とする。`RELEASE.md` のシェル例 `codex/prepare-$PMR_VERSION` は、同じ手順を手元で進めるときの名前である。
- `evidence/` はコミットしない。実測 JSON・API 応答・ログも Git に含めない。
- 配布準備 PR と参照更新 PR は draft で開く。マージ、承認、Ready への変更、自動マージの有効化はしない。
- CI が失敗しても製品ソースは変更しない。ローカルの `npm run build` で `dist/` を差し替えない。
- 配布準備 PR を開いたあと、その PR が main へマージされるまで待つ。コメント、レビュー、PR 上の CI 成功では工程3へ進まない。
- CI の完了を待ち、未完了のまま次の工程へ進まない。結果を確認できない場合は、確認できた URL を残して終了する。
- 工程3の確認が全て一致したあとだけ、`RELEASE.md` のとおり注釈付きタグを push する。タグの削除、移動、force push はしない。push が権限で拒否された場合は、確認結果と push するコマンドを残して終了する。
- `gh release create` と Release workflow の手動実行はしない。公開はタグ push で起動する Release workflow に任せる。
- 工程4が一致してから工程5の参照更新 PR を開き、そこで終了する。運用 workflow が旧版の SHA を指しているときは、SHA だけを新コミットへ付け替えない。`docs/workflow.md` の移行手順を同じ PR で完了し、`npm run check:workflows` が成功することを確認する。
- 失敗したときは `RELEASE.md` の「失敗時の対応」に従う。既存タグは動かさない。

1. 工程1を実行する。起動メッセージの SHA が main の祖先であり、その SHA への main の push CI が工程1の条件を全て満たすことを確認する。満たさなければ終了する。
2. 工程2を実行する。artifact を配置して検証し、配布準備 PR を draft で開く。ステージした差分は `dist/` の3ファイルだけにする。
3. レビューされ、main へマージされるまで待つ。
4. 工程3を実行する。マージコミットを `PMR_RELEASE_SHA` とし、main の push CI とソース差分を確認する。製品ソース・依存・ビルド設定が変わっていればタグを作らず終了する。確認後にタグを push し、Release workflow の `verify` と `publish` の両方が成功するまで待つ。失敗した run の URL を残し、タグは削除も移動もしない。
5. 工程4を実行する。タグが指すコミット、Immutable Release、asset、タグ内の `dist/` が一致することを確認する。
6. 工程5を実行する。参照更新 PR を draft で開いて終了する。マージしない。

## 開始前に確認すること

- 公開するソース変更が main にマージされている。
- 作業ツリーに未コミットの変更がない。Git・GitHub CLI はホスト、npm・配布ファイルの操作は Node.js 24 の Dev Container で実行する。
- タグと Release（Draft を含む）の両方で未使用の `vMAJOR.MINOR.PATCH` を決めている。先頭ゼロ、prerelease、build metadata は使用できない。ルートの `package.json` の version はタグとは独立している。
- リポジトリで Immutable Releases が有効になっている。公開 job の `GITHUB_TOKEN` には `contents: write` が必要。

手順全体で次の2つの SHA を区別します。**利用側の `uses:` に固定するのは配布用 SHA です。**

| 変数              | 指すコミット                             | 用途                                     |
| ----------------- | ---------------------------------------- | ---------------------------------------- |
| `PMR_SOURCE_SHA`  | artifact を生成した main CI の `headSha` | ビルド元を記録し、配布準備ブランチを作る |
| `PMR_RELEASE_SHA` | 配布準備 PR を main にマージしたコミット | タグを付け、利用側の `uses:` に固定する  |

以下のコマンドはリポジトリのルートで、同じシェルから順に実行します。バージョン、SHA、run ID の例は実際の値に置き換えてください。コマンドが失敗したり確認結果が一致しなかったりした場合は、原因を解消してから次に進みます。

## 1. ソースと main CI を選ぶ

```sh
PMR_REPO=nimiusrd/pr-merge-readiness-action
PMR_VERSION=v0.8.0  # 例。実際に公開する未使用のバージョンを指定する。
PMR_SOURCE_SHA='CIで検証した40桁SHAに置き換える'

git status --short
git fetch origin main
git merge-base --is-ancestor "$PMR_SOURCE_SHA" origin/main
gh run list --repo "$PMR_REPO" --workflow ci.yml --branch main --event push \
  --commit "$PMR_SOURCE_SHA" --limit 10
PMR_RUN_ID=123456789
gh run view "$PMR_RUN_ID" --repo "$PMR_REPO" \
  --json url,workflowName,headBranch,headSha,event,status,conclusion,jobs
```

選んだ run が以下を全て満たすことを確認します。

- `workflowName` が `CI`、`headBranch` が `main`、`event` が `push`。
- `headSha` が `PMR_SOURCE_SHA` と一致する。
- `status` が `completed`、`conclusion` が `success` で、`test`・`bundle` の両 job が成功している。
- `action-bundle` artifact をダウンロードできる。保存期間は7日間。

PR の CI artifact は配布に使いません。artifact が期限切れなら、同じソース SHA の main CI を再実行するか、新しい main CI を選び直します。ローカルビルドで代用しません。

## 2. 配布準備 PR を作成・マージする

### artifact を配置・検証する

ソース SHA からブランチを作り、未使用のディレクトリに artifact をダウンロードします。PR 本文や証跡は配布ファイル用のディレクトリの外に保存します。

```sh
PMR_PREP_DIR="evidence/release-$PMR_VERSION-$PMR_RUN_ID"
PMR_BUNDLE_DIR="$PMR_PREP_DIR/action-bundle"
devcontainer exec --workspace-folder . test ! -e "$PMR_PREP_DIR"
git switch -c "codex/prepare-$PMR_VERSION" "$PMR_SOURCE_SHA"
gh run download "$PMR_RUN_ID" --repo "$PMR_REPO" \
  --name action-bundle --dir "$PMR_BUNDLE_DIR"

devcontainer exec --workspace-folder . npm ci
devcontainer exec --workspace-folder . npm run check:dist -- "$PMR_BUNDLE_DIR"
devcontainer exec --workspace-folder . --remote-env "PMR_BUNDLE_DIR=$PMR_BUNDLE_DIR" bash -euo pipefail -c '
mkdir -p dist
for file in index.js package.json SHA256SUMS; do
  install -m 644 "$PMR_BUNDLE_DIR/$file" "dist/$file"
done
'
devcontainer exec --workspace-folder . npm run check:dist
devcontainer exec --workspace-folder . env PMR_TEST_BUNDLE=dist/index.js \
  node --import tsx --test tests/bundle.test.ts
git add -f dist/index.js dist/package.json dist/SHA256SUMS
git add -u -- dist/
git diff --cached --stat
git diff --cached --name-status
```

`dist/` は後述の3ファイルだけにします。余分なファイルがあれば内容を確認して除去し、構成・checksum・バンドルテストを再確認してください。バンドルテストは `node_modules` のない一時ディレクトリで、入力検証と HTTP による Action 実行を確認します。

### PR を提出する

提出前に[開発チェック](README.md#開発)を実行し、ステージした差分が配布物だけであることを確認します。以下の本文例の値を置き換え、その版の変更要約と検証結果を追記して、`$PMR_PREP_DIR/pr.md` に保存してください。

```markdown
## 概要

<バージョン> の配布物を main CI artifact から配置する。
ソース SHA: <PMR_SOURCE_SHA>
CI run: <main CI run URL>

## 検証

main の push CI で test・bundle の成功を確認。
ダウンロード後と配置後の構成・checksum、および同梱バンドルのテストを確認。
```

```sh
git commit -m "$PMR_VERSION の配布物を準備する"
git push -u origin "codex/prepare-$PMR_VERSION"
gh pr create --repo "$PMR_REPO" --base main --head "codex/prepare-$PMR_VERSION" \
  --title "$PMR_VERSION の配布準備" --body-file "$PMR_PREP_DIR/pr.md"
```

ソース変更は混ぜません。PR の CI `test`・`bundle` とレビューが完了してから、手動で main にマージします。CI `bundle` は、追跡済み `dist/` の checksum と同梱バンドルを検証した後、別の `build/dist/` に開発用バンドルを生成します。

## 3. マージ後の CI を確認し、タグを push する

配布準備 PR をマージしたコミットを指定し、そのコミットに対する main の push CI を確認します。PR の CI 成功だけでは、この工程の完了になりません。

```sh
PMR_RELEASE_SHA='配布準備PRをマージした40桁SHAに置き換える'
git fetch origin main
git merge-base --is-ancestor "$PMR_SOURCE_SHA" "$PMR_RELEASE_SHA"
git merge-base --is-ancestor "$PMR_RELEASE_SHA" origin/main
git diff --name-status "$PMR_SOURCE_SHA" "$PMR_RELEASE_SHA" -- . ':(exclude)dist/**'

gh run list --repo "$PMR_REPO" --workflow ci.yml --branch main --event push \
  --commit "$PMR_RELEASE_SHA" --limit 5
PMR_MERGE_RUN_ID=123456790
gh run view "$PMR_MERGE_RUN_ID" --repo "$PMR_REPO" \
  --json url,workflowName,headBranch,headSha,event,status,conclusion,jobs
```

工程1と同じ条件で CI 全体の成功を確認し、`headSha` が `PMR_RELEASE_SHA` と一致することを確かめます。差分もレビューし、**artifact のソース SHA からタグ対象コミットまでに、製品ソース・依存・ビルド設定が変わっていたら、変更後の main CI artifact で配布準備をやり直します。** checksum とバンドルテストだけでは、ソースとの完全な対応を証明できません。

確認後、タグを作成して push します。タグ対象は main の先端でなくても、履歴に含まれていれば公開できます。

```sh
git tag -a "$PMR_VERSION" "$PMR_RELEASE_SHA" -m "$PMR_VERSION"
git push origin "refs/tags/$PMR_VERSION"
gh run list --repo "$PMR_REPO" --workflow release.yml --event push --limit 5
# タグ・対象コミット・開始時刻を照合して run ID を指定する。
PMR_RELEASE_RUN_ID=123456791
gh run view "$PMR_RELEASE_RUN_ID" --repo "$PMR_REPO" \
  --json url,headBranch,headSha,event,startedAt
gh run watch "$PMR_RELEASE_RUN_ID" --repo "$PMR_REPO" --exit-status
```

Release workflow の役割は次のとおりです。再ビルドや main・タグ・配布物のコミット更新は行いません。

| job       | 処理                                                                                                                                                                                 |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `verify`  | タグ形式、リモートタグとイベントのコミット、main の履歴、配布物の構成・checksum を確認し、ソースのテスト・静的検査とタグ内バンドルのテストを実行する                                 |
| `publish` | 同じタグ・配布物を再確認し、タグ内の3ファイルを tar.gz にまとめて Release を公開する。成功後の Summary にバージョン・コード表記の40桁配布用 SHA・Release run URL・固定参照を記載する |

Release 本文と Summary の配布用 SHA と `owner/name@<40桁SHA>` の固定参照は、コード表記で40桁を表示します。SHA を素の文で書くと表示が短縮されるためです。配布ブランチは記載しません。

## 4. 公開結果を確認する

```sh
gh release view "$PMR_VERSION" --repo "$PMR_REPO" \
  --json url,tagName,isDraft,isImmutable,assets,body
git fetch origin main "refs/tags/$PMR_VERSION:refs/tags/$PMR_VERSION"
git rev-parse "$PMR_VERSION^{commit}"
git merge-base --is-ancestor "$PMR_VERSION^{commit}" origin/main
git ls-tree -r "$PMR_VERSION^{commit}" dist/
```

- 通常公開では Release run の `verify`・`publish` が成功している。手動復旧時は元の `verify` 成功を確認し、復旧内容と検証結果を記録する。
- `tagName` が `PMR_VERSION`、`isDraft` が `false`、`isImmutable` が `true` で、asset に `pr-merge-readiness-action.tar.gz` がある。
- タグが指す40桁のコミット SHA が `PMR_RELEASE_SHA`、Release 本文、公開 Summary（手動復旧時は復旧記録）と一致する。注釈付きタグ自身の SHA ではなく、`^{commit}` で得る SHA を使う。
- タグ内の `dist/` が3ファイルだけで、全て Git mode `100644` になっている。

## 5. 参照更新 PR と公開記録を作る

公開確認後、最新の main から別の PR を作り、以下の SHA・バージョンコメントを同じ配布用 SHA に更新します。

- `README.md` の公開版の案内と導入例
- `examples/pr-merge-readiness.yml`
- `.github/workflows/pr-merge-readiness.yml`

参照更新 PR の本文には、バージョン・変更要約・配布準備 PR・main CI run URL とソース SHA・マージ後の CI run URL・Release run URL と配布用 SHA・検証要約を記載します。実測 JSON・API 応答・ログは Git 管理せず、`evidence/` または Actions artifacts に保存します。

## 失敗時の対応

まず run のログ、リモートタグ、Release の状態を確認します。通信・認証エラーだけで Release 未作成と判断しません。

```sh
gh run view "$PMR_RELEASE_RUN_ID" --repo "$PMR_REPO" --log-failed
git ls-remote origin "refs/tags/$PMR_VERSION" "refs/tags/$PMR_VERSION^{}"
gh release view "$PMR_VERSION" --repo "$PMR_REPO" \
  --json url,tagName,isDraft,isImmutable,assets,body
```

| 状態                                                 | 対応                                                                                                                            |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| タグ・配布物・ソースの検証で失敗                     | 修正した配布準備 PR をマージし、新しいバージョンで公開する。既存タグは移動しない                                                |
| 一時的な通信障害で、Release が存在しないと確認できた | リモートタグのコミット SHA が同じことを確認し、元の run を再実行する                                                            |
| Draft がある                                         | 元の `verify` 成功とタグの SHA を確認し、タグ内の配布物と既存 asset を照合する。不足する asset を添付し、Draft を手動で公開する |
| Release が公開済み                                   | 工程4の確認を行う。不具合は新しいバージョンで修正する                                                                           |

公開スクリプトは常に `gh release create` を実行するため、既存 Draft や公開済み Release がある状態では再実行で復旧できません。[GitHub CLI は asset を伴う作成時に Draft 作成・asset 添付・公開を順に行う](https://cli.github.com/manual/gh_release_create)ため、その途中で失敗すると Draft が残ることがあります。

手動復旧や、公開済みなのに job が失敗した場合は、元の `publish` 成功や Summary を完了条件にしません。元の `verify` 成功と工程4のタグ・Release・asset を確認し、復旧記録に Release URL・元の run URL・配布用 SHA・対応内容・検証要約を残します。

Draft の復旧用 tar.gz は、検証済みタグ内の3ファイルから作ります。既存 asset は展開したファイルと `SHA256SUMS` を照合してください。tar のメタデータ等で archive 自体の checksum は変わる場合があります。再ビルド、別コミットの配布物への差し替え、公開済み asset の上書き・削除は行いません。[Immutable Release のタグと asset は公開後に変更できません。](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)

## 配布物と開発時の検証

TypeScript を esbuild で依存ごと JavaScript にバンドルします。`action.yml` は `runs.using: node24` で `dist/index.js` を起動します。利用側は Node.js 24 対応 runner を使い、checkout、Node.js のセットアップ、依存解決、ビルド、asset のダウンロードは不要です。OS・CPU ごとの実行ファイルは配布しません。

| タグ内のファイル    | 用途                          |
| ------------------- | ----------------------------- |
| `dist/index.js`     | Action の実行入口             |
| `dist/package.json` | バンドル自身の ES module 宣言 |
| `dist/SHA256SUMS`   | 上記2ファイルの SHA-256       |

全て通常のファイルとし、symlink は許可しません。Release asset はこの3ファイルを梱包した `pr-merge-readiness-action.tar.gz` です。ソースと依存の正本は `.node-version`・ルートの `package.json`・`package-lock.json` です。

開発時は[開発チェック](README.md#開発)に続けて、次を実行します。

```sh
devcontainer exec --workspace-folder . npm run build
devcontainer exec --workspace-folder . npm run check:dist -- build/dist
devcontainer exec --workspace-folder . npm run test:bundle
```

出力先は Git 管理外の `build/dist/` です。実装 PR にはローカル生成物を含めず、追跡済み `dist/` は main CI artifact を使う配布準備 PR で更新します。設定は workflow の `with` に指定し、設定ファイル・設定検証 CLI は提供しません。
