# PR Merge Readiness

GitHub の PR・レビュー・CI・変更履歴を読み取り、マージ準備状況を判定する Composite Action です。任意のリポジトリの workflow から `uses:` で呼び出し、PR ごとの JSON と Job Summary を生成できます。参考 Check と手動ラベルの公開にも対応しています。

対応環境は GitHub.com、Ubuntu、Python 3.14、Git です。Python は uv 0.12.13 で管理し、Action・CI・Dev Container で同じ minor を使用します。実行時の Python 依存パッケージはありません。

## クイックスタート

1. [最小設定](examples/minimal.toml)を自分のリポジトリの `.github/pr-merge-readiness.toml` にコピーします。`ci.workflows` と `ci.required_checks` を自分の CI の名前・発行元に合わせてください。
2. 次の workflow を `.github/workflows/pr-merge-readiness.yml` として追加します。
3. 設定と workflow を default branch に反映し、Actions の **Run workflow** から実行します。PR 番号を空欄にすると、全 open PR を観測します。

```yaml
name: PR Merge Readiness
on:
  workflow_dispatch:
    inputs:
      pr-number:
        description: PR 番号（空欄なら全 open PR）
        type: string
        default: ''
permissions: {}
jobs:
  observe:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: read
      checks: read
      statuses: read
    steps:
      - uses: nimiusrd/pr-merge-readiness-action@b3a6259c4b6dae30ebf5fd60c0aeac5ead0819f7 # v0.3.0
        with:
          operation: observe
          action-ref: b3a6259c4b6dae30ebf5fd60c0aeac5ead0819f7
          pr-number: ${{ inputs.pr-number }}
          report-dir: readiness-report
          artifact-name: pr-merge-readiness-${{ github.run_id }}-${{ github.run_attempt }}
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
        if: always()
        with:
          name: pr-merge-readiness-${{ github.run_id }}-${{ github.run_attempt }}
          path: readiness-report/
          if-no-files-found: error
          retention-days: 30
```

`uses:`、入力 `action-ref`、設定 `action_ref` には、同じ release の **40 桁 commit SHA** を指定します。この例は [v0.3.0](https://github.com/nimiusrd/pr-merge-readiness-action/releases/tag/v0.3.0) の SHA に固定しています。更新時は 3 箇所を合わせて変更してください。

Action が Python と自身の実装を用意し、呼び出したリポジトリの設定と PR を GitHub API から読み取ります。利用側に Python の導入、Action ソースのコピー、checkout step は必要ありません。設定は default branch から読み、その commit SHA を出力します。

結果は各 PR の判定として Job Summary と artifact に残ります。`observe` の成功は観測処理の成功を表し、マージ条件の充足を意味しません。

## Check・ラベルを公開する

`operation` ごとに step を組み合わせて利用します。[通常の workflow の完全な例](examples/pr-merge-readiness.yml)をコピーすると、CI・PR イベント、未観測表示、観測、Check、手動ラベル、設定検証を導入できます。再利用可能 workflow と workflow 生成 CLI は提供しません。

`prepare` が default branch の設定 SHA を一度確定し、イベントと設定から後続処理を選択します。観測と公開には同じ `config-sha` 出力を渡してください。公開には同じ run ID・attempt の artifact を渡します。公開直前にも head/base・状態を確認し、遅延結果で新しい表示を戻しません。

観測は read-only、Check とラベルは別 job の限定権限で実行します。Check を更新する全 job の concurrency group は `autonomous-merge-check-writer` に統一してください。手動ラベル更新の全体は `autonomous-merge-labels` で直列化します。

`publish-labels` は `workflow_dispatch` の `update-labels = true` と全 open PR の観測を要求します。PR 番号指定とは併用できません。Check が有効なら観測 → Check → ラベルの順序で job を構成し、Check 公開失敗後はラベルを更新しないでください。完全な例にはこの条件を含めています。

[workflow の設定・更新手順](docs/workflow.md)に、イベント対応表と設定検証の方法を記載しています。

## 設定 version 1

```toml
version = 1
action_ref = "b3a6259c4b6dae30ebf5fd60c0aeac5ead0819f7" # v0.3.0

[ci]
workflows = ["CI"]

[review]
minimum_approvals = 0
require_resolved_threads = true
stale_change_review_days = 30

[publication]
checks = true
labels = "manual"

[[ci.required_checks]]
kind = "check_run"
name = "test"
app_id = 15368
```

`ci.workflows` は CI 開始・完了イベントを選ぶ workflow の表示名です。`required_checks` は判定対象の job／Check 名です。Check Run は `kind = "check_run"` と `app_id`、commit status は `kind = "status"` と `creator` を指定します。空の必須 Check、重複、未知キー、不正型、非固定 SHA は拒否します。

`review` の全項目は必須です。承認数は 0 以上、レビュー閾値は正の整数です。`publication` は省略でき、既定値は `checks = true`、`labels = "manual"`。ラベルのもう一つの値は `"off"` です。

[複数 workflow の設定例](examples/multiple-workflows.toml)は、架空の Build・Quality・Security workflow と 7 個の Check を組み合わせています。

この設定例では、変更された既存ファイルの base 上の最終変更から、観測時点で **30 日を超える**場合、現在 head に対する人間の承認を要求します。ちょうど 30 日では発動しません。承認者は User かつ OWNER／MEMBER／COLLABORATOR、レビューは現在 head の有効な APPROVED である必要があります。Bot、外部ユーザー、旧 head、dismiss されたレビューは解除条件になりません。追加ファイルは過去履歴を持たず、rename は旧パスの履歴を使います。

履歴 API は 1 PR あたり最大 100 ファイル、run 全体で最大 100 要求です。失敗要求も消費し、同じ base SHA・パスは PR 間で cache を共有します。別 base、別 run に cache は引き継ぎません。上限超過・API 失敗・履歴不正は情報不足として扱います。

## 判定と表示

判定は `SHADOW_CONDITIONS_MET`、`WAITING`、`HUMAN_REVIEW_REQUIRED`、`INSUFFICIENT_DATA` の 4 種類です。Check の表示名は `Autonomous Merge Shadow / PR #番号`、conclusion は常に `neutral` です。マージや承認、branch protection の変更は行わず、必須 Check として登録する用途ではありません。

## Composite Action の入力・出力

ルートの `action.yml` が提供するインターフェースです。

| 入力 | 契約 |
| --- | --- |
| `operation` | 必須。`prepare`／`validate-config`／`observe`／`mark`／`publish-checks`／`publish-labels` |
| `action-ref` | 必須。Action 自身の 40 桁 SHA |
| `config-path` | default branch の設定パス。既定 `.github/pr-merge-readiness.toml` |
| `config-sha` | 公開・`validate-config` では必須、`prepare` では禁止。観測・`mark` には準備の出力を渡す。省略時は default branch を解決 |
| `repository` | workflow の repository と一致すること。既定 `github.repository` |
| `pr-number` | 手動 `observe` だけで使う任意の正整数 |
| `event-path` | `observe`／`mark` 用。省略時 `GITHUB_EVENT_PATH` |
| `report-dir` | 観測・公開で必須。`mark` では禁止 |
| `artifact-name` | 観測・公開で必須。`pr-merge-readiness-RUN_ID-ATTEMPT`。`mark` では禁止 |
| `token` | 必須の権限を持つ token。既定 `github.token` |

`prepare` と `validate-config` は `pr-number`・`event-path`・`report-dir`・`artifact-name` を受け取りません。`prepare` は実イベントの `pr-number` と `update-labels` を検証し、`operation`（`mark`／`observe`／`skip`）、`checks`、`labels`、`pr-number`、`config-sha` を出力します。`checks` と `labels` は `"true"`／`"false"` の文字列です。`validate-config` は明示した commit の TOML を read-only で検証し、設定 SHA だけを出力します。運用用の設定確定には使いません。

`publish-*` は `pr-number` と `event-path` を受け取りません。`publish-labels` は実イベントの明示的な手動入力と、全 open PR 観測を要求します。観測・公開の出力は `report-dir`、`manifest`、`artifact-name`、`config-sha`。`mark` の出力は設定 SHA だけです。複数 PR の判定を boolean に集約しません。

`observe` は `workflow_dispatch`、対象 `workflow_run` の完了、`pull_request_target` の終了で使用します。`mark` は対象 CI の開始または PR の状態変更用です。直接利用する場合もこのイベント契約に従って step を振り分けてください。[イベント対応表](docs/workflow.md#イベント)を参照できます。

| operation | job の token 権限 |
| --- | --- |
| `prepare`・`validate-config` | `contents: read` |
| `observe` | `contents: read`、`pull-requests: read`、`checks: read`、`statuses: read` |
| `mark` | `contents: read`、`pull-requests: read`、`checks: write`、`actions: read` |
| `publish-checks` | `contents: read`、`pull-requests: read`、`checks: write` |
| `publish-labels` | `contents: read`、`pull-requests: write`、`issues: write` |

Action は固定版の uv で Python 3.14 を用意し、自身の `run.sh` から `python -I -B` で起動します。起動時は利用側の `pyproject.toml`、`uv.toml`、`.python-version`、仮想環境を参照しません。利用側の Python モジュール、`PYTHONPATH`、`sitecustomize`、PR ソースを実行しません。入力値は環境変数を経由し、シェルコードに直接展開しません。

## レポートとオフライン再評価

artifact 名は run ID と attempt ごとに分け、保持期間を 30 日に設定します。PR 別 `pr-番号.json`、`manifest.json`、`summary.md` を保存し、収集失敗時にも今回の JSON と Summary を残します。別 run／attempt の artifact を探す fallback はありません。

同じ run の全 job 再実行では、前 attempt の artifact が取得できなくなる場合があります。[upload-artifact #585](https://github.com/actions/upload-artifact/issues/585) に、名前を attempt ごとに分けた場合も含む報告があります。再観測には新しい **Run workflow** を使い、再実行する場合は必要な artifact を先に Git 外へ保存してください。30日の保存設定は、GitHub 上で削除・再実行された artifact の再取得を保証しません。

レポートは `format = "pr-merge-readiness/report"`、manifest は `format = "pr-merge-readiness/manifest"`、いずれも `schema_version = 1` です。レポートには観測、正規化 policy、policy の SHA-256 指紋、判定、conditions、出所を保存します。出所は評価器・設定の repository／SHA／path です。`provenance.workflow` は常に null です。manifest は repository、run ID、attempt、artifact 名、観測範囲、PR 別ファイル一覧、収集失敗状態、同じ出所を持ちます。公開前に一覧の全ファイルを検証します。

新しい artifact を展開し、その Action SHA を含む、利用者が信頼したローカル Git リポジトリを指定します。

```sh
bash /absolute/path/to/pr-merge-readiness-action/run.sh replay \
  --report /absolute/path/to/artifact/pr-123.json \
  --source-dir /absolute/path/to/trusted-action-git \
  --source-repository nimiusrd/pr-merge-readiness-action \
  --action-sha <信頼済み40桁SHA>
```

保存した出所と指定 SHA を照合し、`git archive` でその commit の評価器と依存モジュールを取り出します。保存情報だけで評価し、通信を禁止した隔離 Python プロセスで判定全体を比較します。現在時刻、現在 checkout、ネットワークは使いません。結果が同じなら `matches: true`、終了コード 0 です。

旧 CLI、policy version 2、旧 JSON／artifact／評価器は非対応です。旧 artifact の変換、旧形式への fallback、旧 SHA との比較はありません。

## 開発

```sh
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . uv sync --locked
devcontainer exec --workspace-folder . uv run --locked pytest
devcontainer exec --workspace-folder . uv run --locked ruff check .
devcontainer exec --workspace-folder . uv run --locked ruff format --check .
devcontainer exec --workspace-folder . uv run --locked mypy
devcontainer exec --workspace-folder . uv run --locked python scripts/check_workflows.py
```

`.python-version` で Python 3.14、`uv.lock` で開発依存関係を固定します。依存更新時は `uv lock --upgrade` で lockfile を更新し、上記検証を実行してください。テストは pytest の関数・fixture・パラメータ化で記述し、Ruff で lint と整形、mypy の strict mode でパッケージ・CLI・検証スクリプトを型検査します。

実測記録には run URL、attempt、対象 SHA、Action SHA、設定 SHA、JSON 名、期待値・実測値を残し、生 JSON は artifact または Git 外に保存します。

不具合や改善提案は[このリポジトリの Issues](https://github.com/nimiusrd/pr-merge-readiness-action/issues)で管理します。

## ライセンス

[MIT License](LICENSE)。著作権と出典は [NOTICE](NOTICE.md) に記載しています。
