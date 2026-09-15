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

ビルド用の依存は `build` group、出力は Git 管理外の `build/` と `dist/` です。通常の `uv sync --locked` は開発・テスト用です。ビルド時は `--group build` を付けます。ローカル Dev Container のビルドはその Linux 環境向けの検証であり、配布物は Release workflow の Ubuntu 22.04 環境で作ります。

バイナリテストは PATH から Python・uv を外し、利用側の Python 設定・モジュールを置いたディレクトリで実行します。TOML 検証、HTTP による設定取得、対象外 PR の省略、引数の受け渡し、信頼済み Git commit の評価器によるオフライン `replay` を確認します。CI は x64・arm64 の両方でこの検証を行います。

## 公開手順

1. 実装 PR を main にマージし、CI のソース検証と両 CPU のバイナリ検証の成功を確認します。
2. Actions の **Release → Run workflow** で main を選び、未使用の `vMAJOR.MINOR.PATCH` を入力します。タグ名は Action の配布版を識別します。Python パッケージを PyPI に公開する処理はありません。
3. `build` job が source のテスト・静的検査、バイナリのビルド・テストを両 CPU で実行します。成功したバイナリと checksum を同じ run の artifact として保存します。
4. `publish` job が同じ run の artifact だけを取得し、checksum と実行権限を確認します。元のソースコミットを親として、2つのバイナリと checksum だけを加えた**配布用コミット**を作ります。このコミットに入力したタグを付け、タグと GitHub Release を公開します。main は進めません。
5. リリースノートの配布用コミット SHA を確認し、利用側の `uses:` と TOML の `action_ref` を同じ40桁 SHA に変更します。このリポジトリ自身の `.github/`・利用例・README と、利用側リポジトリの固定 SHA も更新します。[移行時の確認](workflow.md#バイナリ版への移行)に従ってマージ後の手動観測を確認します。

ソースコミットと配布用コミットの関係は、`ソース SHA → dist/ のみ追加した配布用 SHA ← リリースタグ` です。リリースノートには両方の SHA を残します。**Action には配布用 SHA を指定してください。** main や実装 PR には生成物を置かないため、ソース SHA を指定すると `Release binary missing` で失敗します。`run.sh` はソースから動かす開発用 CLI として残します。

ビルド job の token は読み取り専用、公開 job だけが `contents: write` と `actions: read` を持ちます。main 以外、無効なバージョン、既存タグ、checkout SHA の不一致、未コミット変更、artifact 不足・checksum 不一致は公開前に拒否します。

タグ公開後に GitHub Release の作成だけが失敗した場合、同じバージョンでの再実行は既存タグとして停止します。タグを動かさず、そのタグの配布用コミットと元の run の検証済み artifact を確認し、同じ配布物で Release 作成を完了してください。別のソースや再ビルドしたバイナリを同じタグへ上書きしません。

## CLI と replay

配布用コミットを checkout すれば `bash run-binary.sh validate-config --config <path>` や `bash run-binary.sh replay ...` を実行できます。Release asset を展開し、実行ファイルを直接呼び出すこともできます。ローカル Composite Action として使う場合は、配布用コミットの checkout と `dist/<platform>/` の配置を維持してください。

`replay` は自身の実行ファイルを子プロセスとして起動し、明示した信頼済み SHA の評価器を `git archive` で取り出して実行します。Python を外部から起動せず、現在の checkout やバイナリ内の評価器に置き換えません。ネットワーク呼び出しは禁止します。外部 Git の起動時は、PyInstaller が変更した `LD_LIBRARY_PATH` を元に戻します。[PyInstaller の実行時の仕様](https://pyinstaller.org/en/stable/runtime-information.html)を参照してください。
