# リリース手順

公開担当者は、**配布準備 PR を手動作成 → main にマージ → タグを push → 公開確認 → 参照更新 PR**の順に進めます。Release workflow の手動実行や配布準備 PR の自動作成は行いません。

正本は [CI](../.github/workflows/ci.yml)、[Release workflow](../.github/workflows/release.yml)、[タグ・配布物の検証](../scripts/validate_release.sh)、[公開スクリプト](../scripts/publish_release.sh)です。

## 配布物と実行環境

TypeScript を esbuild で依存ごと JavaScript にバンドルします。実行時は GitHub Actions runner の Node.js 24 を使用します。利用側での checkout、Node.js のセットアップ、依存解決、ビルド、asset のダウンロードは不要です。OS・CPU ごとの実行ファイルは配布しません。Node.js 24 対応 runner を使用してください。

タグ内の `dist/` は次の3ファイルだけです。全て通常のファイルとし、symlink を許可しません。

| ファイル | 用途 |
| --- | --- |
| `index.js` | `action.yml` が指定する Action の実行入口 |
| `package.json` | バンドル自身の ES module 宣言 |
| `SHA256SUMS` | 上記2ファイルの SHA-256 |

Release asset は `pr-merge-readiness-action.tar.gz` です。ソースと依存の正本は `.node-version`・ルートの `package.json`・`package-lock.json` です。閾値は `with.stale-change-review-days` に指定し、設定ファイルは使用しません。

## 開発時の検証

[開発チェック](../README.md#開発)の後、次を実行します。

```sh
devcontainer exec --workspace-folder . npm run build
devcontainer exec --workspace-folder . npm run check:dist -- build/dist
devcontainer exec --workspace-folder . npm run test:bundle
```

出力先は Git 管理外の `build/dist/` です。追跡済み `dist/` はローカルビルドで更新しません。バンドルテストは `node_modules` のない一時ディレクトリに配布ファイルだけを配置し、入力検証と HTTP による Action 実行を確認します。

## 配布準備 PR を手動で作成する

1. 公開するソースを main にマージし、そのコミットの CI `test`・`bundle` が成功するのを待ちます。main の push CI は検証済み `action-bundle` artifact を7日間保存します。PR の CI artifact は配布に使いません。
2. 未使用の `vMAJOR.MINOR.PATCH` を決めます。先頭ゼロ、prerelease、build metadata は受け付けません。`package.json` の version はタグとは独立しています。
3. CI run URL・ソース SHA を記録し、そのソースコミットから配布準備ブランチを作ります。以下の値は実際の値に置き換えます。Git と `gh` はホストで実行します。

```sh
PMR_REPO=nimiusrd/pr-merge-readiness-action
PMR_VERSION=v0.8.0  # 例。実際に公開する未使用のバージョンを指定する。
PMR_SOURCE_SHA='CIで検証した40桁SHAに置き換える'
PMR_RUN_ID=123456789
PMR_PREP_DIR="evidence/release-$PMR_VERSION-$PMR_RUN_ID"
gh run view "$PMR_RUN_ID" --repo "$PMR_REPO" --json url,headSha,event,conclusion
# headSha が PMR_SOURCE_SHA、event が push、CI 全体が成功であることを確認する。
git fetch origin main
git switch -c "codex/prepare-$PMR_VERSION" "$PMR_SOURCE_SHA"
gh run download "$PMR_RUN_ID" --repo "$PMR_REPO" --name action-bundle --dir "$PMR_PREP_DIR"
```

4. ダウンロード先は既存ファイルを含まないディレクトリを使います。artifact の構成と checksum を確認し、そのまま `dist/` に配置します。

```sh
devcontainer exec --workspace-folder . npm run check:dist -- "$PMR_PREP_DIR"
devcontainer exec --workspace-folder . --remote-env "PMR_PREP_DIR=$PMR_PREP_DIR" bash -euo pipefail -c '
mkdir -p dist
for file in index.js package.json SHA256SUMS; do
  install -m 644 "$PMR_PREP_DIR/$file" "dist/$file"
done
'
git rm --ignore-unmatch dist/cli.js dist/THIRD_PARTY_LICENSES.txt
git add -f dist/index.js dist/package.json dist/SHA256SUMS
```

5. バージョン・CI run URL・ソース SHA・変更と検証の要約を記載して、手動で配布準備 PR を作ります。ソース変更は混ぜません。CI `bundle` は配布物がある場合、ローカルでのビルドより先に checksum と同梱バンドルのテストを実行します。CI 成功後に main にマージします。

**artifact のソース SHA からタグ対象コミットまでに、製品ソース・依存 lock・ビルド設定の変更がないことをレビューで確認してください。** 変更や artifact の期限切れがあれば、新しい main CI artifact で準備し直します。checksum とバンドルテストだけではソースとの完全な対応を証明できません。

### TypeScript 版の初回配布

移植の実装コミットには旧 `dist/linux-*` の削除だけを含め、新しいローカル生成物は含めません。この段階の `action.yml` が参照する `dist/index.js` は未配置です。実装コミットを利用側の `uses:` に指定せず、main CI artifact の配布準備と公開を完了してから利用してください。公開済みの Python 版タグ・運用 workflow の参照 SHA はそのまま保持します。

## タグを push して公開する

配布準備 PR のマージ後の CI 成功、Immutable Releases の有効化、公開 job の `GITHUB_TOKEN` の `contents: write` 権限を確認します。

```sh
PMR_RELEASE_SHA='配布準備PRをマージした40桁SHAに置き換える'
git fetch origin main
git merge-base --is-ancestor "$PMR_RELEASE_SHA" origin/main
git tag -a "$PMR_VERSION" "$PMR_RELEASE_SHA" -m "$PMR_VERSION"
git push origin "refs/tags/$PMR_VERSION"
gh run list --repo "$PMR_REPO" --workflow release.yml --event push --limit 5
# タグ・対象コミット・開始時刻を照合して run ID を指定する。
PMR_RELEASE_RUN_ID=123456790
gh run watch "$PMR_RELEASE_RUN_ID" --repo "$PMR_REPO" --exit-status
```

`verify` はタグ形式、リモートタグのコミット、main 履歴に含まれること、配布物の構成と checksum を確認します。その後ソースのテスト・静的検査と、タグ内の `dist/index.js` のバンドルテストを実行します。Release workflow は再ビルドしません。

`publish` は同じタグと配布物を再確認し、タグ内の3ファイルを tar.gz にまとめて公開します。main・タグ・配布物のコミットを変更しません。公開成功後の Summary にバージョン・配布用 SHA・Release run URL を記載します。タグ対象は main の先端でなくても履歴に含まれていれば公開できます。

## 公開後の確認と参照更新

- Release run の `verify`・`publish` が成功している。
- Release が公開済みかつ Immutable で、`pr-merge-readiness-action.tar.gz` を含んでいる。
- タグ内の `dist/` が上記3ファイルだけで、タグの40桁 SHA が公開 Summary と一致する。

```sh
gh release view "$PMR_VERSION" --repo "$PMR_REPO" --json url,tagName,isDraft,isImmutable,assets,body
git fetch origin main "refs/tags/$PMR_VERSION:refs/tags/$PMR_VERSION"
git rev-parse "$PMR_VERSION^{commit}"
git merge-base --is-ancestor "$PMR_VERSION^{commit}" origin/main
```

確認後、`README.md` の公開版の案内と導入例、`examples/pr-merge-readiness.yml`、`.github/workflows/pr-merge-readiness.yml` の SHA・バージョンコメントを同じ配布用 SHA に更新します。初回は README の Python 版・未公開の案内も更新します。公開記録には配布準備 PR・main CI run URL とソース SHA・Release run URL と配布用 SHA・検証要約を記載します。実測 JSON・API 応答・ログは Git 管理せず `evidence/` または Actions artifacts に保存します。

## 失敗時の対応

最初に run のログとリモートタグ・Release を確認します。通信・認証エラーだけで Release 未作成と判断しません。

| 状態 | 対応 |
| --- | --- |
| タグ・配布物・検証で失敗 | 修正した配布準備 PR をマージし、新しいバージョンで公開する。既存タグは移動しない |
| 一時的な通信障害で Release 未作成 | タグの SHA が同じことを確認して元の run を再実行する |
| Draft がある | 検証済みタグの配布物と既存 asset を照合し、不足する asset を添付して Draft を公開する |
| Release 公開済み | 公開後の確認を行う。不具合は新しいバージョンで修正する |

Draft の復旧では元の `verify` 成功を確認し、タグ内の3ファイルから tar.gz を作成します。再ビルド、別コミットの配布物への差し替え、公開済み asset の上書きや削除は行いません。

## 公開記録

新しいリリース用の配布ブランチは作りません。既存 v0.4.0 は旧方式で公開したため、タグと `codex/releases/v0.4.0` は配布用 SHA `107e80a91574e277ea3c13e41aeff7710cae77e2` を指したまま保持します。過去のタグ・コミットは書き換えず、v0.5.0〜v0.7.0 は旧 Release workflow が main に配布物をコミットしてタグを作成しました。今後は手動の配布準備 PR とタグ push で公開します。

[v0.5.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/34994639892)では、ソース `e6d305d05cce80eae0411cfb33845b4aef8a6e58` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `4790eda7e56840c18a98a1d4c135ab03c119b5f2` を main とタグに公開しました。`labels = "auto"` に対応する最初のリリースです。

[v0.5.1 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35000713461)では、ソース `2fdf61530ceec44a788cd13b9a0c00ef22cbbe2a` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `38abf77191ca0801d10dd6bd8c9d387bdad4b911` を main とタグに公開しました。Immutable Release として公開済みで、TOML の `action_ref` と Action SHA の一致制約を削除しています。

[v0.6.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35113264395)では、ソース `001c3be5f3c8e43009033331eaac03f6f15c5683` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `568c7441e16afa46db11bc84f1e4708e6525a404` を公開しました。設定 version 3 と単一実行への簡素化、更新時刻だけの変化による観測失敗の修正を含みます。

[v0.7.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35344205607)では、ソース `55b6a9488b8928bd89d0279198b14c2322ca9dcf` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `fc422aad51c2a719cc7b625afb5e3939b4f52868` を公開しました。設定 version 4 に対応し、GitHub Ruleset と重複する判定を削除しています。設定と参照 SHA を同時に移行してください。

## CLI

設定検証 CLI は廃止しました。`with` 入力の検証は Action の実行時に行います。
