# リリース手順

公開担当者は、**配布準備 PR を手動作成 → main にマージ → タグを push → 公開確認 → 参照更新 PR**の順に進めます。Release workflow の手動実行や、配布準備 PR の自動作成は行いません。

実行内容の正本は [CI](../.github/workflows/ci.yml)、[Release workflow](../.github/workflows/release.yml)、[タグ・配布物の検証](../scripts/validate_release.sh)、[公開スクリプト](../scripts/publish_release.sh)です。

## 配布物と対応環境

Python 3.14・uv 0.12.13・`uv.lock` の PyInstaller 6.22.3 で、Python 同梱の単一実行ファイルを生成します。配布対象は Ubuntu 26.04 以降の Linux x64 です。arm64・Windows・macOS は対象外です。

| ビルド runner | タグ内のファイル | Release asset |
| --- | --- | --- |
| `ubuntu-26.04` | `dist/linux-x64/pr-merge-readiness`、`dist/linux-x64/SHA256SUMS` | `pr-merge-readiness-linux-x64.tar.gz` |

Composite Action は `run-binary.sh` からタグ内のバイナリを起動します。実行時に asset のダウンロード、uv・Python の導入、依存解決、ビルドは行いません。利用側の job には `runs-on: ubuntu-26.04` などの x64 runner を指定します。Linux ではビルド環境の glibc より古い環境での動作を保証しません。

## 開発時の検証

```sh
devcontainer exec --workspace-folder . uv sync --locked --group build
devcontainer exec --workspace-folder . uv run --locked --group build python scripts/build_binary.py
devcontainer exec --workspace-folder . --remote-env PMR_TEST_BINARY=dist/linux-x64/pr-merge-readiness \
  uv run --locked --group build pytest tests/test_binary.py
```

ビルド依存は `build` group、作業用ファイルは Git 管理外の `build/`、出力先は `dist/` です。Dev Container は `linux/amd64` に固定しており、Apple Silicon では Docker のエミュレーションを使います。ローカルビルドは開発用の検証に使い、配布物には CI の Ubuntu 26.04 runner で検証した artifact を使います。通常の実装 PR にローカルで再ビルドした `dist/` の差分を含めません。

バイナリテストは PATH から Python・uv を外し、利用側の Python 設定・モジュールを置いたディレクトリで実行します。TOML 検証、HTTP による設定取得、対象外 PR の省略、引数の受け渡しを確認します。

## 配布準備 PR を手動で作成する

1. 公開するソースを main にマージし、そのコミットの CI の `test`・`binary` が両方成功するのを待ちます。main の push で動いた CI は、検証済みの `binary-linux-x64` artifact を7日間保存します。PR の CI からは配布用 artifact を取得しません。
2. 未使用の `vMAJOR.MINOR.PATCH` を決めます。先頭ゼロ、prerelease、build metadata は受け付けません。`pyproject.toml` の Python パッケージ version、設定 TOML の `version` は別の値なので、タグ名に合わせるだけの更新は不要です。PyPI 公開もありません。
3. 成功した CI の run URL・ソース SHA を控え、そのソースコミットから配布準備用のブランチを作ります。以下の値は対象の run・バージョン・40桁 SHA に置き換え、Git と `gh` はホストで実行します。

```sh
PMR_REPO=nimiusrd/pr-merge-readiness-action
PMR_VERSION=v0.7.1  # 例。実際に公開する未使用のバージョンに置き換える。
PMR_SOURCE_SHA='CIで検証した40桁SHAに置き換える'
PMR_RUN_ID=123456789  # 成功した main の CI run ID
PMR_PREP_DIR="evidence/release-$PMR_VERSION-$PMR_RUN_ID"
gh run view "$PMR_RUN_ID" --repo "$PMR_REPO" --json url,headSha,event,conclusion
# headSha と PMR_SOURCE_SHA の一致、push イベント、CI 全体の成功を確認する。
git fetch origin main
git switch -c "codex/prepare-$PMR_VERSION" "$PMR_SOURCE_SHA"
gh run download "$PMR_RUN_ID" --repo "$PMR_REPO" \
  --name binary-linux-x64 --dir "$PMR_PREP_DIR/linux-x64"
```

ダウンロード先は既存ファイルを含まないディレクトリを使います。Dev Container で checksum を確認し、同じバイナリを配置します。artifact のダウンロードでは実行権限が保持されないため、`install` で権限を設定します。

```sh
devcontainer exec --workspace-folder . \
  --remote-env "PMR_PREP_DIR=$PMR_PREP_DIR" bash -euo pipefail -c '
artifact="$PMR_PREP_DIR/linux-x64"
(cd "$artifact" && sha256sum --check SHA256SUMS)
install -D -m 755 "$artifact/pr-merge-readiness" dist/linux-x64/pr-merge-readiness
install -m 644 "$artifact/SHA256SUMS" dist/linux-x64/SHA256SUMS
'
git rm -r --ignore-unmatch dist/linux-arm64
git add -f dist/linux-x64/pr-merge-readiness dist/linux-x64/SHA256SUMS
```

4. 配布準備 PR の本文に、バージョン・CI run URL・ソース SHA・変更と検証の要約を記載して手動で PR を作成します。`dist/` は上記2ファイルだけにし、初回は旧 `dist/linux-arm64/` を削除します。過去のタグは変更しません。実測 JSON・API 応答・ログは `evidence/` または Actions artifacts に保存し、Git 管理する文書には URL と要約だけを記載します。
5. [開発チェック](../README.md#開発)を実行し、PR の CI 成功後に main へマージします。CI の `binary` job は `dist/` の変更を検出すると、再ビルドする前に PR 内の checksum とバイナリを検証します。その後ソースからのビルドとバイナリテストも行います。配布準備 PR にソース変更を混ぜず、コミット対象が取得した artifact と一致することを確認します。

ローカルの Dev Container は Ubuntu 24.04 のため、Ubuntu 26.04 用の配布物の動作確認には CI を使います。配布準備中にローカルビルドで取得済み artifact を上書きしないでください。

**artifact のソース SHA からタグ対象コミットまでに、製品ソースやビルド設定の変更が入っていないことをレビューで確認してください。** 途中で変更された場合は、新しいソースの main CI artifact で配布準備 PR を更新します。checksum とバイナリテストだけでは、ソースとの完全な対応は証明できません。artifact が期限切れの場合も CI で再ビルド・検証してから準備します。

## タグを push して公開する

公開前に、配布準備 PR のマージ後の CI 成功、Immutable Releases の有効化、公開 job の `GITHUB_TOKEN` による Release 作成権限（`contents: write`）を確認します。Release workflow は main への push やタグ作成を行わないため、公開用の branch ruleset bypass は不要です。

1. 配布準備 PR をマージしたコミットを指定してタグを作成し、push します。軽量タグ・注釈付きタグの両方に対応します。以下は注釈付きタグの例です。

```sh
PMR_RELEASE_SHA='配布準備PRをマージした40桁SHAに置き換える'
git fetch origin main
git merge-base --is-ancestor "$PMR_RELEASE_SHA" origin/main
git tag -a "$PMR_VERSION" "$PMR_RELEASE_SHA" -m "$PMR_VERSION"
git push origin "refs/tags/$PMR_VERSION"
gh run list --repo "$PMR_REPO" --workflow release.yml --event push --limit 5
# タグ・対象コミット・開始時刻を照合し、Release run ID を指定する。
PMR_RELEASE_RUN_ID=123456790
gh run watch "$PMR_RELEASE_RUN_ID" --repo "$PMR_REPO" --exit-status
```

タグはローカルで付けるだけでは開始せず、GitHub への push が必要です。上記のように公開担当者が push してください。`GITHUB_TOKEN` によるタグ push は別の workflow を起動しません。

2. `verify` job がタグ形式・リモートタグの指すコミット・main の履歴にあること・配布物の構成と checksum を確認します。その後ソースのテスト・静的検査と、**タグに同梱されたバイナリ**のテストを実行します。バイナリは再ビルドしません。
3. `publish` job が同じタグと配布物を再確認し、タグ内のバイナリと checksum を tar.gz にまとめて GitHub Release を公開します。main・タグ・配布物のコミットは変更しません。公開成功後、サマリーにバージョン・配布用 SHA・Release run URL を記載します。

タグ対象は main の先端でなくても、履歴に含まれていれば公開できます。公開中に main が進んでも対象は変わりません。利用側の Action は必ず**リリースタグが指す40桁 SHA**に固定します。任意の main の SHA は配布用バイナリとソースの対応を保証しません。

## 公開後の確認と参照更新

- [ ] Release run の `verify` と `publish` が成功している。
- [ ] Release が Draft ではなく公開済みで、Immutable になっている。
- [ ] Release asset に `pr-merge-readiness-linux-x64.tar.gz` がある。
- [ ] タグ内の `dist/` は `linux-x64` の実行ファイルと checksum だけになっている。
- [ ] タグが指す40桁 SHA とサマリー・リリースノートの配布用 SHA が一致し、main の履歴に含まれている。

```sh
gh release view "$PMR_VERSION" --repo "$PMR_REPO" \
  --json url,tagName,isDraft,isImmutable,assets,body
git fetch origin main "refs/tags/$PMR_VERSION:refs/tags/$PMR_VERSION"
git rev-parse "$PMR_VERSION^{commit}"
git merge-base --is-ancestor "$PMR_VERSION^{commit}" origin/main
```

確認後は参照更新用 PR で次をまとめて更新し、開発チェックと CI を通します。

| 更新対象 | 更新内容 |
| --- | --- |
| `README.md` | 配布版の案内・Release リンク・導入例の SHA とバージョンコメント |
| `examples/pr-merge-readiness.yml` | README の導入例と同じ SHA・バージョンコメント |
| `.github/workflows/pr-merge-readiness.yml` | このリポジトリで使用する Action の SHA・バージョンコメント |
| `.github/pr-merge-readiness.toml`・`examples/*.toml`・設定の文書 | 設定仕様が変わった場合だけ更新 |
| この文書の公開記録 | バージョン・配布準備 PR・ビルド元 CI run URL とソース SHA・Release run URL と配布用 SHA・検証と変更の要約 |

設定 version が変わる場合は、[移行手順](workflow.md#version-3-から-version-4-への移行)に従って設定と workflow を同時に更新します。新しい設定だけを旧バイナリへ先行導入しません。

## 失敗時の対応

最初に run のログとリモートのタグ・Release を確認します。通信・認証エラーを Release 未作成と判断しないでください。タグは公開開始前から存在し、workflow 失敗時もそのまま残ります。

| 状態 | 対応 |
| --- | --- |
| タグの形式・main との関係・配布物・検証で失敗 | 原因を修正した配布準備 PR をマージし、新しいバージョンのタグで公開する。既存タグは移動しない |
| 一時的な通信障害などで失敗、Release 未作成 | タグが元のコミットを指すことを確認し、元の Release run を再実行できる。タグ内の同じ配布物を再検証する |
| Release が Draft | 下記の手順で既存 Draft の公開を完了する。workflow の再実行では Draft を更新しない |
| Release 公開済み | 公開後の確認を行う。不具合は新しいバージョンで修正し、公開済みのタグ・asset は変更しない |

### Release 作成の途中で失敗した場合

1. 元の Release run の `verify` 成功を確認し、タグが検証した配布用 SHA を指し、main の履歴にあることを照合します。
2. Release 未作成なら元の run を再実行します。すでに Draft がある場合はその Draft を使用します。
3. Draft に asset が不足する場合は、検証済みタグのファイルから以下のように tar.gz を作ります。再ビルドや別コミットのバイナリへの差し替えは行いません。

```sh
PMR_RECOVERY_DIR="evidence/recovery-$PMR_VERSION"
devcontainer exec --workspace-folder . \
  --remote-env "PMR_VERSION=$PMR_VERSION" \
  --remote-env "PMR_RECOVERY_DIR=$PMR_RECOVERY_DIR" bash -euo pipefail -c '
mkdir -p "$PMR_RECOVERY_DIR/linux-x64"
for file in pr-merge-readiness SHA256SUMS; do
  git show "$PMR_VERSION:dist/linux-x64/$file" > "$PMR_RECOVERY_DIR/linux-x64/$file"
done
(cd "$PMR_RECOVERY_DIR/linux-x64" && sha256sum --check SHA256SUMS)
chmod 755 "$PMR_RECOVERY_DIR/linux-x64/pr-merge-readiness"
chmod 644 "$PMR_RECOVERY_DIR/linux-x64/SHA256SUMS"
tar -C "$PMR_RECOVERY_DIR/linux-x64" -czf "$PMR_RECOVERY_DIR/pr-merge-readiness-linux-x64.tar.gz" \
  pr-merge-readiness SHA256SUMS
'
```

復旧用ディレクトリは既存ファイルを含まないものを使います。タグから取得するため、配布準備 CI の artifact 保持期限に依存しません。

4. Draft の既存 asset はダウンロード・展開し、タグ内のバイナリ・checksum と一致することを確認します。不一致なら公開を止めて原因を調べます。不足している asset を添付し、リリースノートにバージョン・配布用 SHA・元の Release run URL・設定要件を記載してから Draft を公開します。
5. 公開後の確認と参照更新を行います。公開済み asset の上書きや削除で復旧しません。

## 公開記録

新しいリリース用の配布ブランチは作りません。既存 v0.4.0 は旧方式で公開したため、タグと `codex/releases/v0.4.0` は配布用 SHA `107e80a91574e277ea3c13e41aeff7710cae77e2` を指したまま保持します。過去のタグ・コミットは書き換えず、v0.5.0〜v0.7.0 は旧 Release workflow が main に配布物をコミットしてタグを作成しました。今後は手動の配布準備 PR とタグ push で公開します。

[v0.5.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/34994639892)では、ソース `e6d305d05cce80eae0411cfb33845b4aef8a6e58` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `4790eda7e56840c18a98a1d4c135ab03c119b5f2` を main とタグに公開しました。`labels = "auto"` に対応する最初のリリースです。

[v0.5.1 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35000713461)では、ソース `2fdf61530ceec44a788cd13b9a0c00ef22cbbe2a` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `38abf77191ca0801d10dd6bd8c9d387bdad4b911` を main とタグに公開しました。Immutable Release として公開済みで、TOML の `action_ref` と Action SHA の一致制約を削除しています。

[v0.6.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35113264395)では、ソース `001c3be5f3c8e43009033331eaac03f6f15c5683` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `568c7441e16afa46db11bc84f1e4708e6525a404` を公開しました。設定 version 3 と単一実行への簡素化、更新時刻だけの変化による観測失敗の修正を含みます。

[v0.7.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35344205607)では、ソース `55b6a9488b8928bd89d0279198b14c2322ca9dcf` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `fc422aad51c2a719cc7b625afb5e3939b4f52868` を公開しました。設定 version 4 に対応し、GitHub Ruleset と重複する判定を削除しています。設定と参照 SHA を同時に移行してください。

## CLI

配布用コミットを checkout すれば `bash run-binary.sh validate-config --config <path>` を実行できます。Release asset を展開し、実行ファイルを直接呼び出して設定を検証することもできます。PR の判定・ラベル更新は、GitHub workflow から40桁 SHA で固定した remote Action を呼び出してください。local Action（`uses: ./path`）は非対応です。
