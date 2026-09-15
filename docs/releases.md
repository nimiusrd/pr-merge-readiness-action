# バイナリのビルドとリリース

Python 3.14 と uv 0.12.13 で開発し、`uv.lock` の PyInstaller 6.22.3 で Python 同梱の単一実行ファイルを生成します。実行時に uv・Python の導入、依存解決、ビルドは行いません。Composite Action は OS・CPU に応じた同梱バイナリを呼び、観測 artifact の保存は引き続き upload-artifact で行います。

## 配布物と対応環境

| runner の CPU | ビルド runner | Action 内の実行ファイル |
| --- | --- | --- |
| x64 | `ubuntu-22.04` | `dist/linux-x64/pr-merge-readiness` |
| arm64 | `ubuntu-22.04-arm` | `dist/linux-arm64/pr-merge-readiness` |

Ubuntu 22.04 以降の Linux x64 / arm64 を対象にします。Windows・macOS は対象外です。PyInstaller は OS・CPU ごとにビルドが必要で、Linux ではビルド環境の glibc より古い環境での動作を保証しません。[PyInstaller のプラットフォーム制約](https://pyinstaller.org/en/stable/usage.html#supporting-multiple-platforms)に従い、各 CPU の Ubuntu 22.04 runner でビルド・検証します。

各バイナリに `SHA256SUMS` を付け、Release assets には `pr-merge-readiness-linux-x64.tar.gz` と `pr-merge-readiness-linux-arm64.tar.gz` を公開します。Action は assets を実行時にダウンロードせず、`uses:` で指定したコミット内の実行ファイルを使います。Git はローカル Action の参照確認と `replay` に必要です。

## 開発時の検証

```sh
devcontainer exec --workspace-folder . uv sync --locked --group build
devcontainer exec --workspace-folder . uv run --locked --group build python scripts/build_binary.py
# Dev Container の CPU に合わせて linux-x64 または linux-arm64 を指定する。
devcontainer exec --workspace-folder . --remote-env PMR_TEST_BINARY=dist/linux-arm64/pr-merge-readiness \
  uv run --locked --group build pytest tests/test_binary.py
```

ビルド用の依存は `build` group、作業用ファイルは Git 管理外の `build/`、バイナリの出力先は `dist/` です。Release workflow が配布用のバイナリと checksum を main で追跡します。`.gitignore` の `dist/` は、それ以外の未追跡の出力を除外するために残します。通常の `uv sync --locked` は開発・テスト用です。ビルド時は `--group build` を付けます。ローカル Dev Container のビルドはその Linux 環境向けの検証であり、配布物は Release workflow の Ubuntu 22.04 環境で作ります。ローカルで再ビルドすると追跡済みファイルにも差分が出るため、その差分を実装 PR に含めないでください。

バイナリテストは PATH から Python・uv を外し、利用側の Python 設定・モジュールを置いたディレクトリで実行します。TOML 検証、HTTP による設定取得、対象外 PR の省略、引数の受け渡し、信頼済み Git commit の評価器によるオフライン `replay` を確認します。CI は x64・arm64 の両方でこの検証を行います。

## 公開手順

1. 実装 PR を main にマージし、CI のソース検証と両 CPU のバイナリ検証の成功を確認します。
2. Actions の **Release → Run workflow** で main を選び、未使用の `vMAJOR.MINOR.PATCH` を入力します。タグ名は Action の配布版を識別します。Python パッケージを PyPI に公開する処理はありません。
3. `build` job が source のテスト・静的検査、バイナリのビルド・テストを両 CPU で実行します。成功したバイナリと checksum を同じ run の artifact として保存します。
4. `publish` job が main の先端とビルド対象のソース SHA の一致を確認し、同じ run の artifact だけを一時ディレクトリに取得します。checksum を検証し、`dist/` の2つのバイナリと checksum を置き換えます。変更があればソースコミットを親とする**配布用コミット**を作り、main の更新とそのコミットを指すタグの作成を atomic push で同時に公開してから、GitHub Release を作成します。バイナリと checksum が既存の内容と同じ場合は、現在の main コミットにタグを付けます。
5. リリースノートの配布用コミット SHA を確認し、利用側の `uses:` と TOML の `action_ref` を同じ40桁 SHA に変更します。このリポジトリ自身の `.github/`・利用例・README と、利用側リポジトリの固定 SHA も更新します。[移行時の確認](workflow.md#バイナリ版への移行)に従ってマージ後の手動観測を確認します。

ソースコミットと配布用コミットはどちらも main の履歴に残ります。リリースノートにはビルド対象のソース SHA と公開した配布用 SHA を記載します。**Action にはリリースタグが指す40桁 SHA を指定してください。** リリース後の main にはソースだけを変更するコミットも入るため、任意の main の SHA では同梱バイナリとソースの対応を保証できません。`run.sh` はソースから動かす開発用 CLI として残します。

新しいリリース用の配布ブランチは作りません。既存 v0.4.0 は旧方式で公開したため、タグと `codex/releases/v0.4.0` は配布用 SHA `107e80a91574e277ea3c13e41aeff7710cae77e2` を指したまま保持します。過去のタグ・コミットは書き換えず、v0.5.0 からこの手順で main に配布物を反映しています。

[v0.5.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/34994639892)では、ソース `e6d305d05cce80eae0411cfb33845b4aef8a6e58` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `4790eda7e56840c18a98a1d4c135ab03c119b5f2` を main とタグに公開しました。`labels = "auto"` に対応する最初のリリースです。

ビルド job の token は読み取り専用、公開 job だけが `contents: write` と `actions: read` を持ちます。main 以外、無効なバージョン、既存タグ、checkout SHA の不一致、未コミット変更、artifact 不足・checksum 不一致は公開前に拒否します。main がビルド対象 SHA から進んだ場合も停止します。確認後に main が進んだ場合やブランチ保護で push が拒否された場合は、通常の fast-forward 制約と atomic push により main とタグをどちらも公開せず終了します。最新 main で新しい run を実行してください。force push や保護設定の変更は行いません。[Git の atomic push](https://git-scm.com/docs/git-push#Documentation/git-push.txt---atomic)を使用します。

現在の main の保護設定は削除・force push の禁止です。Release workflow の `GITHUB_TOKEN` で main に通常の push を行います。この token による push は新たな CI を起動しないため、公開の根拠は同じ Release run 内で完了したソース・両 CPU のバイナリ検証です。[GitHub のイベント起動仕様](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow#triggering-a-workflow-from-a-workflow)を参照してください。

main・タグの公開後に GitHub Release の作成だけが失敗した場合、同じバージョンでの再実行は既存タグとして停止します。公開済み main を巻き戻さず、タグが指す配布用コミットと元の run の検証済み artifact を確認し、同じ配布物で Release 作成を完了してください。タグの移動や、別のソース・再ビルドしたバイナリによる同一バージョンの上書きは行いません。

## CLI と replay

配布用コミットを checkout すれば `bash run-binary.sh validate-config --config <path>` や `bash run-binary.sh replay ...` を実行できます。Release asset を展開し、実行ファイルを直接呼び出すこともできます。ローカル Composite Action として使う場合は、配布用コミットの checkout と `dist/<platform>/` の配置を維持してください。

`replay` は自身の実行ファイルを子プロセスとして起動し、明示した信頼済み SHA の評価器を `git archive` で取り出して実行します。Python を外部から起動せず、現在の checkout やバイナリ内の評価器に置き換えません。ネットワーク呼び出しは禁止します。外部 Git の起動時は、PyInstaller が変更した `LD_LIBRARY_PATH` を元に戻します。[PyInstaller の実行時の仕様](https://pyinstaller.org/en/stable/runtime-information.html)を参照してください。
