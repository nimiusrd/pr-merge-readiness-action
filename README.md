# PR Merge Readiness

PR のレビュー・変更履歴を確認し、対応が必要かをラベルと Actions の実行サマリーに表示する GitHub Action です。

設定 version 3 に対応する [v0.6.0](https://github.com/nimiusrd/pr-merge-readiness-action/releases/tag/v0.6.0) を公開しています。旧版からの更新は[移行手順](docs/workflow.md#version-2-からの移行)を参照してください。

## 目次

- [概要](#概要)
- [導入](#導入)
- [設定](#設定)
- [判定内容](#判定内容)
- [ラベル](#ラベル)
- [実行の流れ](#実行の流れ)
- [入力と出力](#入力と出力)
- [開発](#開発)
- [ライセンス](#ライセンス)

## 概要

承認・変更要求・未解決スレッド・変更履歴からレビュー条件を判定します。コードの差分本文は評価せず、自動マージ・自動承認は行いません。

| 対象 | 扱い |
| --- | --- |
| 同一リポジトリからの通常 PR | 自動で判定し、管理ラベルを更新する |
| fork・Dependabot | 自動処理を省略する。手動同期では残っている管理ラベルを除去する |
| Draft・競合・PR の状態 | ラベルではなく実行サマリーで示す |
| CI の結果 | GitHub Checks で確認する |
| その他のラベル | 変更しない |

利用側の checkout、Python・uv のセットアップは不要です。

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

手動実行の対象は次のとおりです。

- PR 番号を指定したとき: その PR のラベルを更新する
- 空欄のとき: 全 open PR を判定し、終了済み PR の管理ラベルも除去する
- 対象がないとき: 成功する

## 設定

設定はレビュー条件の3項目だけです。全項目が必須で、承認数は0以上、日数は正の整数を指定します。不要なキーや旧版の設定はエラーになります。

```toml
version = 3

[review]
minimum_approvals = 0
require_resolved_threads = true
stale_change_review_days = 30
```

[承認を1件必須にする例](examples/review-policy.toml)もあります。

## 判定内容

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

ラベルはレビューと変更履歴の条件を表します。Draft・競合・PR の状態はサマリーで別に示します。

## 実行の流れ

Action は1ステップで、設定の検証 → 観測 → サマリー生成 → ラベル更新を行います。

### 設定の扱い

PR イベントでは提案された設定を検証し、実際の判定には default branch で一度確定した設定を使用します。実行サマリーには使用した設定コミットも表示します。

### 成功と失敗

| 状況 | 結果 |
| --- | --- |
| 必要な承認を待っているだけ | 成功する |
| PR ごとの情報不足 | サマリーと `shadow/再観測が必要` で示し、Action は失敗する |
| 観測中に head が変わった | 失敗し、ラベルは更新しない |
| 観測後に head が変わった | 更新を省略する |

JSON artifact・参考 Check・オフライン再評価は提供しません。

### 自動実行しない場合

タイトル・本文だけの編集、レビュー投稿・CI 完了では自動実行しません。競合中の PR も `pull_request` では起動しないため、再評価には Run workflow を使います。

起動条件や移行の詳細は[運用と移行](docs/workflow.md)を参照してください。

## 入力と出力

通常は入力を省略できます。利用先は実行中のリポジトリ、対象 PR はイベントから決定します。

| 入力 | 既定値・用途 |
| --- | --- |
| `config-path` | `.github/pr-merge-readiness.toml` |
| `token` | `github.token` |

| 出力 | 内容 |
| --- | --- |
| `operation` | `observe` / `validate-config` / `skip` |
| `config-sha` | 読み取った設定コミット |

Action の版は `uses:` の40桁 SHA だけで指定します。実際の参照を GitHub のコンテキストから取得し、40桁 SHA と提供元を確認します。local Action（`uses: ./path`）は非対応です。`operation` を入力で指定する機能はありません。

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

## ライセンス

[MIT License](LICENSE) / [著作権と出典](NOTICE.md)
