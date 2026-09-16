# PR Merge Readiness

PR のレビュー・変更履歴を確認し、対応が必要かをラベルと Actions の実行サマリーに表示する GitHub Action です。

設定 version 3 に対応する [v0.6.0](https://github.com/nimiusrd/pr-merge-readiness-action/releases/tag/v0.6.0) を公開しています。旧版からの更新は[移行手順](docs/workflow.md#version-2-からの移行)を参照してください。

## 導入

1. [最小設定](examples/minimal.toml)を `.github/pr-merge-readiness.toml` にコピーします。
2. 次の workflow を `.github/workflows/pr-merge-readiness.yml` に保存します。
3. default branch に反映します。以降は PR の更新時に実行され、Actions の **Run workflow** からも更新できます。

```yaml
# uses をリリースタグが指す40桁 SHA に固定します。
name: PR Merge Readiness

on:
  pull_request:
    types: [opened, reopened, synchronize, edited, ready_for_review, converted_to_draft, closed]
  workflow_dispatch:
    inputs:
      pr-number:
        description: PR 番号（空欄なら全 open PR と終了済み PR のラベルを同期）
        type: string
        default: ''
  push:
    paths: [.github/pr-merge-readiness.toml, .github/workflows/pr-merge-readiness.yml]

permissions: {}

jobs:
  readiness:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    concurrency:
      group: autonomous-merge-check-writer
      cancel-in-progress: false
      queue: max
    permissions:
      contents: read
      pull-requests: write
      issues: write
    steps:
      - uses: nimiusrd/pr-merge-readiness-action@568c7441e16afa46db11bc84f1e4708e6525a404 # v0.6.0
```

PR 番号を指定した手動実行は、その PR のラベルを更新します。空欄なら全 open PR を判定し、終了済み PR の管理ラベルも除去します。対象がない場合は成功します。利用側の checkout、Python・uv のセットアップは不要です。

## 設定

```toml
version = 3

[review]
minimum_approvals = 0
require_resolved_threads = true
stale_change_review_days = 30
```

設定はレビュー条件の3項目だけです。全項目が必須で、承認数は0以上、日数は正の整数を指定します。不要なキーや旧版の設定はエラーになります。[承認を1件必須にする例](examples/review-policy.toml)もあります。

- 現在の head に対する承認を数えます。コメントだけのレビューは直前の承認・変更要求を取り消しません。
- 変更要求、未解決スレッド、CI 定義ファイルの変更は対応事項になります。
- 既存ファイルの最終変更から指定日数を**超える**場合、現在の head に対する人間の承認を要求します。承認者は OWNER・MEMBER・COLLABORATOR のいずれかです。追加ファイルは対象外、rename は旧パスの履歴を使います。

## ラベル

| ラベル | 意味 |
| --- | --- |
| `shadow/要対応事項なし` | 自動確認の範囲で、承認待ち・未解決の指摘・追加確認事項がない |
| `shadow/レビュー待ち` | 必要な承認を待っている |
| `shadow/要対応` | 変更要求・未解決スレッド・変更履歴・CI 定義の変更への対応が必要 |
| `shadow/再観測が必要` | 情報不足、または観測・公開の間にレビュー対象が変わった |

ラベルはレビューと変更履歴の条件を表します。Draft・競合・PR の状態はサマリーで別に示します。CI の結果は GitHub Checks で確認してください。コードの差分本文は評価せず、自動マージ・自動承認は行いません。

同一リポジトリからの PR が自動更新の対象です。fork・Dependabot は自動処理を省略し、手動同期では残っている管理ラベルを除去します。その他のラベルには触れません。

## 実行の流れ

Action は1ステップで、設定の検証 → 観測 → サマリー生成 → ラベル更新を行います。

PR イベントでは提案された設定を検証し、実際の判定には default branch で一度確定した設定を使用します。観測中に head が変わると失敗し、ラベルは更新しません。観測後に head が変わった場合は更新を省略します。

PR ごとの情報不足はサマリーと `shadow/再観測が必要` で示し、Action は失敗にします。必要な承認を待っているだけなら処理は成功です。実行サマリーには使用した設定コミットも表示します。JSON artifact・参考 Check・オフライン再評価は提供しません。

タイトル・本文だけの編集、レビュー投稿・CI 完了では自動実行しません。競合中の PR も `pull_request` では起動しないため、再評価には Run workflow を使います。起動条件や移行の詳細は[運用と移行](docs/workflow.md)を参照してください。

## Action の入力・出力

通常は入力を省略できます。利用先は実行中のリポジトリ、対象 PR はイベントから決定します。

| 入力 | 既定値・用途 |
| --- | --- |
| `config-path` | `.github/pr-merge-readiness.toml` |
| `token` | `github.token` |

Action の版は `uses:` の40桁 SHA だけで指定します。実際の参照を GitHub のコンテキストから取得し、40桁 SHA と提供元を確認します。local Action（`uses: ./path`）は非対応です。

出力は `operation`（`observe` / `validate-config` / `skip`）と `config-sha`（読み取った設定コミット）です。`operation` を入力で指定する機能はありません。

## 開発

Ubuntu 22.04 以降の Linux x64 / arm64 に対応します。開発では Python 3.14・uv を使い、配布時は PyInstaller で Python を同梱します。

```sh
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . uv sync --locked
devcontainer exec --workspace-folder . uv run --locked pytest
devcontainer exec --workspace-folder . uv run --locked ruff check .
devcontainer exec --workspace-folder . uv run --locked ruff format --check .
devcontainer exec --workspace-folder . uv run --locked mypy
devcontainer exec --workspace-folder . uv run --locked python scripts/check_workflows.py
```

ローカル設定の検証は `bash run.sh validate-config --config <path>`、配布バイナリの検証・公開は[リリース手順](docs/releases.md)を参照してください。

[MIT License](LICENSE) / [著作権と出典](NOTICE.md)
