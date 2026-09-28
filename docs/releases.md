# リリース手順

公開担当者は、[公開前の確認](#公開前の確認) → [公開手順](#公開手順) → [公開後の確認と参照更新](#公開後の確認と参照更新)の順に進めます。途中で失敗した場合は、[失敗時の対応](#失敗時の対応)でタグと Release の状態を確認します。

実行内容の正本は [Release workflow](../.github/workflows/release.yml) と [公開スクリプト](../scripts/publish_release.sh)です。

Python 3.14 と uv 0.12.13 で開発し、`uv.lock` の PyInstaller 6.22.3 で Python 同梱の単一実行ファイルを生成します。実行時に uv・Python の導入、依存解決、ビルドは行いません。Composite Action は Linux x64 の同梱バイナリを1回呼び、ラベルと実行サマリーを更新します。

## 配布物と対応環境

| runner の CPU | ビルド runner | Action 内の実行ファイル |
| --- | --- | --- |
| x64 | `ubuntu-22.04` | `dist/linux-x64/pr-merge-readiness` |

Ubuntu 22.04 以降の Linux x64 のみを対象にします。arm64・Windows・macOS は対象外です。利用側の job で x64 の runner（例: `runs-on: ubuntu-latest`）を指定してください。Composite Action 自身は runner を指定できないため、起動スクリプトでも OS・CPU を確認します。Linux ではビルド環境の glibc より古い環境での動作を保証しません。[PyInstaller のプラットフォーム制約](https://pyinstaller.org/en/stable/usage.html#supporting-multiple-platforms)に従い、x64 の Ubuntu 22.04 runner でビルド・検証します。

バイナリに `SHA256SUMS` を付け、Release asset として `pr-merge-readiness-linux-x64.tar.gz` を公開します。Action は asset を実行時にダウンロードせず、`uses:` で指定したコミット内の実行ファイルを使います。Action の実行に Git は不要です。

この変更後のリリースから x64 のみに絞ります。Release workflow が配布用コミットを作る際に既存の `dist/linux-arm64/` も削除します。過去のリリースタグと assets はそのまま保持します。

## 開発時の検証

```sh
devcontainer exec --workspace-folder . uv sync --locked --group build
devcontainer exec --workspace-folder . uv run --locked --group build python scripts/build_binary.py
devcontainer exec --workspace-folder . --remote-env PMR_TEST_BINARY=dist/linux-x64/pr-merge-readiness \
  uv run --locked --group build pytest tests/test_binary.py
```

ビルド用の依存は `build` group、作業用ファイルは Git 管理外の `build/`、バイナリの出力先は `dist/` です。Release workflow が配布用のバイナリと checksum を main で追跡します。`.gitignore` の `dist/` は、それ以外の未追跡の出力を除外するために残します。通常の `uv sync --locked` は開発・テスト用です。ビルド時は `--group build` を付けます。ローカル Dev Container のビルドはその Linux 環境向けの検証であり、配布物は Release workflow の Ubuntu 22.04 環境で作ります。ローカルで再ビルドすると追跡済みファイルにも差分が出るため、その差分を実装 PR に含めないでください。

Dev Container は `linux/amd64` に固定しています。Apple Silicon では Docker のエミュレーションを使用します。既存の arm64 コンテナは Rebuild Container で再作成してください。ビルドスクリプトは x64 以外での生成を拒否します。

バイナリテストは PATH から Python・uv を外し、利用側の Python 設定・モジュールを置いたディレクトリで実行します。TOML 検証、HTTP による設定取得、対象外 PR の省略、引数の受け渡しを確認します。CI は x64 でこの検証を行います。

## 公開前の確認

- [ ] 実装 PR を main にマージし、そのコミットの CI（ソース検証・x64 のバイナリ検証）が成功している。
- [ ] 未使用の `vMAJOR.MINOR.PATCH` を決め、変更点と必要な設定移行を確認している。先頭ゼロ、prerelease、build metadata は受け付けない。
- [ ] ローカルで再ビルドした `dist/` の差分を実装 PR に含めていない。配布物は Release workflow が作る。
- [ ] main への通常 push とタグ作成を Release workflow の `GITHUB_TOKEN` で行える。ビルドは `contents: read`、公開は `contents: write` と `actions: read` を使用する。
- [ ] リポジトリの Immutable Releases が有効であることを Settings で確認している。公開後は同じタグ・asset の差し替えで修正しない。

タグは Action の配布版を識別します。`pyproject.toml` の Python パッケージ version と設定 TOML の `version` は別の値です。リリース番号に合わせるだけの変更は不要で、PyPI への公開処理もありません。

本リポジトリは main への直接 push を含む配布方式です。PR 必須化などの保護を追加した場合は公開方式との整合性を事前に確認します。公開失敗を回避するための force push や保護設定の変更は行いません。[Ruleset と配布方式の関係](rulesets.md#適用と運用)を参照してください。

## 公開手順

1. Actions の **Release → Run workflow** で branch に `main` を選び、決めたバージョンを入力します。タグを先に作る必要はありません。
2. 開始した run の URL とソース SHA を控えます。公開完了まで main の更新を避けます。Release 同士は直列実行されますが、通常の PR マージは止まりません。
3. `build` job が成功するのを確認します。ソースのテスト・静的検査、x64 バイナリのビルド・テストを行い、`binary-linux-x64` を同じ run の artifact として7日間保存します。
4. `publish` job の成功を確認します。同じ run の artifact を取得して checksum を検証し、`dist/linux-x64/` のバイナリと checksum を置き換え、追跡済みの `dist/linux-arm64/` を削除します。変更があればソースコミットを親とする**配布用コミット**を作り、main の更新とタグ作成を atomic push で同時に公開します。内容が既存と同じなら現在の main にタグを付けます。
5. 続いて GitHub Release と x64 の tar.gz asset が公開されます。run のサマリーとリリースノートにはバージョン・ソース SHA・配布用 SHA・検証 run URL が残ります。サマリーはタグ公開後に書くため、サマリーがあるだけでは Release 作成の成功を意味しません。

CLI から開始する場合は、ホストで以下を実行します。`PMR_VERSION` は今回公開する未使用のバージョンへ置き換えてください。UI と CLI のどちらか一方で1回だけ開始します。

```sh
PMR_REPO=nimiusrd/pr-merge-readiness-action
PMR_VERSION=v0.7.1  # 入力例。実際に公開するバージョンへ置き換える。
gh workflow run release.yml --repo "$PMR_REPO" --ref main -f version="$PMR_VERSION"
gh run list --repo "$PMR_REPO" --workflow release.yml --event workflow_dispatch --limit 5
# 開始時刻・ソース SHA・入力バージョンを Actions 画面で照合し、その run ID を指定する。
PMR_RUN_ID=123456789
gh run watch "$PMR_RUN_ID" --repo "$PMR_REPO" --exit-status
```

ソースコミットと配布用コミットはどちらも main の履歴に残ります。リリースノートにはビルド対象のソース SHA と公開した配布用 SHA を記載します。**Action にはリリースタグが指す40桁 SHA を指定してください。** リリース後の main にはソースだけを変更するコミットも入るため、任意の main の SHA では同梱バイナリとソースの対応を保証できません。`run.sh` はソースから動かす開発用 CLI として残します。

`GITHUB_TOKEN` による push は新たな CI を起動しないため、公開の根拠は同じ Release run 内で完了したソース・x64 のバイナリ検証です。[GitHub のイベント起動仕様](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow#triggering-a-workflow-from-a-workflow)を参照してください。

## 公開後の確認と参照更新

- [ ] Release run の `build` と `publish` が成功している。
- [ ] Release が Draft ではなく公開済みで、Immutable になっている。
- [ ] Release asset に `pr-merge-readiness-linux-x64.tar.gz` があり、タグ内の `dist/` は `linux-x64` のみになっている。
- [ ] タグが指す40桁 SHA と、サマリー・リリースノートの配布用 SHA が一致している。そのコミットが main の履歴に含まれている。

ホストでリモートの状態を確認できます。`PMR_REPO`・`PMR_VERSION` は公開した値を使います。

```sh
gh release view "$PMR_VERSION" --repo "$PMR_REPO" \
  --json url,tagName,isDraft,isImmutable,assets,body
git fetch origin main "refs/tags/$PMR_VERSION:refs/tags/$PMR_VERSION"
git rev-parse "$PMR_VERSION^{commit}"
git merge-base --is-ancestor "$PMR_VERSION^{commit}" origin/main
```

確認後は、参照更新用の PR で次をまとめて変更します。

| 更新対象 | 更新内容 |
| --- | --- |
| `README.md` | 配布版の案内・Release リンク・導入例の SHA とバージョンコメント |
| `examples/pr-merge-readiness.yml` | README の導入例と同じ SHA・バージョンコメント |
| `.github/workflows/pr-merge-readiness.yml` | このリポジトリで使用する Action の SHA・バージョンコメント |
| `.github/pr-merge-readiness.toml`・`examples/*.toml`・設定の文書 | 設定仕様が変わった場合だけ、配布版の仕様と整合させる |
| この文書の[公開記録](#公開記録) | バージョン・run URL・ソース SHA・配布用 SHA・検証と変更の要約 |

設定 version が変わる場合は、[移行手順](workflow.md#version-3-から-version-4-への移行)に従って設定と workflow を同時に更新します。新しい設定だけを旧バイナリへ先行導入しないでください。参照更新 PR でも [開発チェック](../README.md#開発)をすべて実行し、CI の成功を確認します。実測 JSON・API 応答・ログは `evidence/` または Actions artifacts に保存し、Git 管理する文書には URL と要約だけを記載します。

## 失敗時の対応

まず run のログと、リモートのタグ・Release の有無を確認します。通信エラーやキャンセルでは、画面上の失敗だけで「未公開」と判断しないでください。

| 失敗した段階・状態 | 対応 |
| --- | --- |
| 入力不正・ビルド・検証の失敗。タグなし | 原因を修正し、最新 main で新しい run を開始する。未公開のバージョンは再利用できる |
| main がビルド対象 SHA から進んだ | 最新 main の CI を確認し、新しい run を開始する。古い run の再実行はソース SHA を更新しない |
| push の拒否。タグなし | 権限・保護設定との整合性を確認する。atomic push で main とタグは両方とも更新されない。force push で回避しない |
| タグあり、Release なし／Draft | 下記の復旧手順で元の検証済み配布物から Release 作成を完了する |
| タグあり、Release 公開済み | 公開後の確認を行う。不具合の修正は新しいバージョンで公開する |

main 以外、無効なバージョン、既存タグ、checkout SHA の不一致、未コミット変更、artifact 不足・checksum 不一致は公開前に拒否します。main の一致確認後に競合した場合も、通常の fast-forward 制約と [atomic push](https://git-scm.com/docs/git-push#Documentation/git-push.txt---atomic)で main・タグの片方だけが公開されることを防ぎます。

### タグ公開後に Release 作成だけが失敗した場合

同じバージョンの workflow 再実行は既存タグのチェックで停止します。公開済み main を巻き戻さず、タグを移動せずに、次の順で復旧します。

1. 元の run の `build` job が成功していることを確認します。サマリー・ログからソース SHA と配布用 SHA を確認し、リモートタグがその配布用 SHA を指すことと main の履歴にあることを照合します。
2. `gh release view` または Releases 画面で公開済み・Draft・未作成を判別します。CLI のエラーが認証・通信障害なら、未作成と判断せず原因を解消します。
3. 元の run の x64 artifact を取得し、checksum とタグ内の配布物の一致を確認して tar.gz を作ります。別 run の artifact や再ビルドしたバイナリは使用しません。

ホストで今回の値を設定し、元の run から取得します。ダウンロード先は既存ファイルを含まないディレクトリを指定してください。

```sh
PMR_REPO=nimiusrd/pr-merge-readiness-action
PMR_VERSION=v0.7.1  # 復旧対象のバージョンへ置き換える。
PMR_RUN_ID=123456789  # 元の Release run ID へ置き換える。
PMR_RECOVERY_DIR="evidence/release-$PMR_VERSION-$PMR_RUN_ID"
git fetch origin main "refs/tags/$PMR_VERSION:refs/tags/$PMR_VERSION"
git rev-parse "$PMR_VERSION^{commit}"
git merge-base --is-ancestor "$PMR_VERSION^{commit}" origin/main
gh run view "$PMR_RUN_ID" --repo "$PMR_REPO"
gh run download "$PMR_RUN_ID" --repo "$PMR_REPO" \
  --name binary-linux-x64 --dir "$PMR_RECOVERY_DIR/linux-x64"
```

取得した配布物を Dev Container で照合・梱包します。archive 内の実行権限も元の公開処理と揃えます。失敗したら先へ進みません。

```sh
devcontainer exec --workspace-folder . \
  --remote-env "PMR_VERSION=$PMR_VERSION" \
  --remote-env "PMR_RECOVERY_DIR=$PMR_RECOVERY_DIR" bash -euo pipefail -c '
artifact="$PMR_RECOVERY_DIR/linux-x64"
(cd "$artifact" && sha256sum --check SHA256SUMS)
for file in pr-merge-readiness SHA256SUMS; do
  git show "$PMR_VERSION:dist/linux-x64/$file" | cmp - "$artifact/$file"
done
chmod 755 "$artifact/pr-merge-readiness"
chmod 644 "$artifact/SHA256SUMS"
tar -C "$artifact" -czf "$PMR_RECOVERY_DIR/pr-merge-readiness-linux-x64.tar.gz" \
  pr-merge-readiness SHA256SUMS
'
```

4. Release が未作成なら、Releases 画面で**既存タグ**を選び Draft を作成します。すでに Draft があるなら、その Draft を使用します。元のサマリーからソース SHA・配布用 SHA・run URL・設定要件をリリースノートへ転記します。
5. 作成した x64 の tar.gz を Draft に添付します。asset がすでに添付されている場合は、ダウンロード・展開して元の artifact とバイナリ・checksum が一致することを確認します。不一致があれば公開を止めて原因を調べます。
6. タグ・ノート・asset を確認して Draft を公開し、[公開後の確認と参照更新](#公開後の確認と参照更新)を行います。

GitHub CLI の [`gh release create`](https://cli.github.com/manual/gh_release_create) は、assets を指定すると Draft 作成・アップロード・公開を順に行います。そのため、途中で失敗すると Draft が残る場合があります。Immutable の保護は公開後に適用されます。公開済みの assets を上書き・削除して復旧しないでください。

artifact の保持期間は7日です。期限切れなどで元の検証済み artifact を取得できない場合、この復旧手順では公開せず、新しいバージョンで検証から実施します。既存タグは残し、同じバージョンへ再ビルドした配布物を割り当てません。

## 公開記録

新しいリリース用の配布ブランチは作りません。既存 v0.4.0 は旧方式で公開したため、タグと `codex/releases/v0.4.0` は配布用 SHA `107e80a91574e277ea3c13e41aeff7710cae77e2` を指したまま保持します。過去のタグ・コミットは書き換えず、v0.5.0 からこの手順で main に配布物を反映しています。

[v0.5.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/34994639892)では、ソース `e6d305d05cce80eae0411cfb33845b4aef8a6e58` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `4790eda7e56840c18a98a1d4c135ab03c119b5f2` を main とタグに公開しました。`labels = "auto"` に対応する最初のリリースです。

[v0.5.1 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35000713461)では、ソース `2fdf61530ceec44a788cd13b9a0c00ef22cbbe2a` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `38abf77191ca0801d10dd6bd8c9d387bdad4b911` を main とタグに公開しました。Immutable Release として公開済みで、TOML の `action_ref` と Action SHA の一致制約を削除しています。

[v0.6.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35113264395)では、ソース `001c3be5f3c8e43009033331eaac03f6f15c5683` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `568c7441e16afa46db11bc84f1e4708e6525a404` を公開しました。設定 version 3 と単一実行への簡素化、更新時刻だけの変化による観測失敗の修正を含みます。

[v0.7.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35344205607)では、ソース `55b6a9488b8928bd89d0279198b14c2322ca9dcf` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `fc422aad51c2a719cc7b625afb5e3939b4f52868` を公開しました。設定 version 4 に対応し、GitHub Ruleset と重複する判定を削除しています。設定と参照 SHA を同時に移行してください。

## CLI

配布用コミットを checkout すれば `bash run-binary.sh validate-config --config <path>` を実行できます。Release asset を展開し、実行ファイルを直接呼び出して設定を検証することもできます。PR の判定・ラベル更新は、GitHub workflow から40桁 SHA で固定した remote Action を呼び出してください。local Action（`uses: ./path`）は非対応です。
