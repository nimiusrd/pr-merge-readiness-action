# PR Merge Readiness

GitHub の標準ルールでは表現できない追加確認事項を、PR の変更履歴などから提示することを責務とする GitHub Action です。マージ条件の強制は GitHub Ruleset に委ねます。

[Action の責務](docs/responsibilities.md)と[推奨 Ruleset](docs/rulesets.md)に役割分担を記載しています。承認数・未解決スレッド・CI の結果・特定パスの担当者レビューは GitHub に委ねます。

このソースは設定 version 4 に対応しています。公開済み [v0.6.0](https://github.com/nimiusrd/pr-merge-readiness-action/releases/tag/v0.6.0) は version 3 用です。以下の利用例は version 4 対応版の公開後に、そのリリースの40桁 SHA を指定して使ってください。[移行手順](docs/workflow.md#version-3-から-version-4-への移行)を参照してください。

## 目次

- [概要](#概要)
- [Action の責務](docs/responsibilities.md)
- [推奨 Ruleset](docs/rulesets.md)
- [導入](#導入)
- [設定](#設定)
- [判定内容](#判定内容)
- [ラベル](#ラベル)
- [実行の流れ](#実行の流れ)
- [入力と出力](#入力と出力)
- [開発](#開発)
- [ライセンス](#ライセンス)

## 概要

変更対象ファイルの履歴から、追加の人間レビューが必要かを判定します。コードの差分本文は評価せず、自動マージ・自動承認は行いません。

| 対象 | 扱い |
| --- | --- |
| 同一リポジトリからの通常 PR | 自動で判定し、管理ラベルを更新する |
| fork・Dependabot | 自動処理を省略する。手動同期では残っている管理ラベルを除去する |
| Draft・競合・レビューの一般条件 | GitHub の PR 画面と Ruleset に委ねる |
| CI の結果 | GitHub Checks で確認する |
| その他のラベル | 変更しない |

利用側の checkout、Python・uv のセットアップは不要です。

## 導入

1. [最小設定](examples/minimal.toml)を `.github/pr-merge-readiness.toml` にコピーします。
2. 次の workflow を `.github/workflows/pr-merge-readiness.yml` に保存します。
3. default branch に反映します。以降は PR の更新時に実行され、Actions の **Run workflow** からも更新できます。

```yaml
# version 4 対応版の公開後、リリースタグが指す40桁 SHA に置き換えます。
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
      - uses: nimiusrd/pr-merge-readiness-action@<RELEASE_COMMIT_SHA>
```

手動実行の対象は次のとおりです。

- PR 番号を指定したとき: その PR のラベルを更新する
- 空欄のとき: 全 open PR を判定し、終了済み PR の管理ラベルも除去する
- 対象がないとき: 成功する

## 設定

設定は変更履歴の閾値だけです。日数は正の整数を指定します。不要なキーや旧版の設定はエラーになります。

```toml
version = 4

[review]
stale_change_review_days = 30
```

[14日を閾値にする例](examples/review-policy.toml)もあります。一般の必要承認数と会話解決は Ruleset で設定します。

## 判定内容

- 既存ファイルが base 側で最後に変更されてから指定日数を**超える**場合、現在の head に対する人間の承認を要求します。承認者は OWNER・MEMBER・COLLABORATOR のいずれかです。追加ファイルは対象外、rename は旧パスの履歴を使います。
- 履歴条件が承認を要求する場合だけレビューを取得します。同じ人の後続の変更要求・承認取り消しは、その人の承認を無効にします。コメントだけのレビューは承認を取り消しません。
- CI 定義を含むどのパスにも同じ履歴条件を適用します。パスだけを理由に追加確認を要求しません。
- 対象コミットや必要な承認情報が観測間に変化した場合、情報不足として再観測を求めます。

## ラベル

| ラベル | 意味 |
| --- | --- |
| `shadow/要対応事項なし` | 変更履歴に基づく追加確認事項がない |
| `shadow/要対応` | 変更履歴に基づく追加の人間レビューが必要 |
| `shadow/再観測が必要` | 情報不足、または観測・公開の間にレビュー対象が変わった |

ラベルとサマリーは追加確認の状態だけを表します。レビュー完了・CI 成功・マージ許可を意味しません。旧 `shadow/レビュー待ち` は次回のラベル同期で除去します。

## 実行の流れ

Action は1ステップで、設定の検証 → 観測 → サマリー生成 → ラベル更新を行います。

### 設定の扱い

PR イベントでは提案された設定を検証し、実際の判定には default branch で一度確定した設定を使用します。実行サマリーには使用した設定コミットも表示します。

### 成功と失敗

| 状況 | 結果 |
| --- | --- |
| 履歴に基づく追加の人間レビューが必要 | 成功する |
| PR ごとの情報不足 | サマリーと `shadow/再観測が必要` で示し、Action は失敗する |
| 観測中に head が変わった | 失敗し、ラベルは更新しない |
| 観測後に head が変わった | 更新を省略する |

JSON artifact・参考 Check・オフライン再評価は提供しません。

### 自動実行しない場合

タイトル・本文だけの編集、レビュー投稿・CI 完了・base branch の更新・時間経過では再観測しません。履歴条件が要求した承認を反映したい場合は Run workflow を使います。競合中の PR も `pull_request` では起動しないため、再評価には Run workflow を使います。

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
