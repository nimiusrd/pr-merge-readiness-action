# PR Merge Readiness

GitHub の PR・レビュー・CI・変更履歴を読み取り、マージ準備状況を判定する Composite Action です。任意のリポジトリの workflow から `uses:` で呼び出し、PR ごとの JSON と Job Summary を生成できます。参考 Check と手動ラベルの公開にも対応しています。

対応環境は GitHub.com、Ubuntu、Python 3.14、Git です。Python は uv 0.12.13 で管理し、Action・CI・Dev Container で同じ minor を使用します。実行時の Python 依存パッケージはありません。

## クイックスタート

1. [最小設定](examples/minimal.toml)を自分のリポジトリの `.github/pr-merge-readiness.toml` にコピーし、CI workflow 名と必須 Check の名前・発行元を合わせます。
2. 次の workflow を `.github/workflows/pr-merge-readiness.yml` として追加します。
3. 設定と workflow を default branch に反映し、Actions の **Run workflow** から実行します。

```yaml
name: PR Merge Readiness
on:
  workflow_dispatch:
    inputs:
      pr-number:
        description: PR 番号（空欄なら全 open PR）
        type: string
        default: ''
      update-labels:
        description: 全 open PR のラベルを更新
        type: boolean
        default: false
permissions: {}

jobs:
  readiness:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    concurrency:
      group: autonomous-merge-check-writer
      cancel-in-progress: false
    permissions:
      contents: read
      actions: read
      statuses: read
      checks: write
      pull-requests: write
      issues: write
    steps:
      - uses: nimiusrd/pr-merge-readiness-action@21fa2df95fc615847ba7e1a24e9c77bd5d1323fb
```

`uses:` と設定の `action_ref` に同じ **40 桁 commit SHA** を指定します。この例は [v0.4.0 の実装コミット](https://github.com/nimiusrd/pr-merge-readiness-action/commit/21fa2df95fc615847ba7e1a24e9c77bd5d1323fb) に固定しています。入力 `action-ref` は実際の参照から取得するため、省略できます。

既定の `operation: run` が、イベントと TOML の設定から設定検証・未観測表示・観測・Check・ラベル更新を選びます。呼び出し側は **1 job・1 step** で利用でき、`if`、`needs`、設定 SHA の受け渡し、artifact の upload/download を組み立てる必要はありません。Python の導入と実装の起動も Action 内で行い、利用側の checkout は不要です。

PR 番号が空なら全 open PR、指定するとその PR を観測します。ラベル更新は `update-labels = true` を明示した場合だけ実行し、PR 番号指定との併用は拒否します。Check が有効なら、観測 → artifact 保存 → Check → ラベルの順に進み、保存や Check 公開の失敗後はラベルを更新しません。

判定と観測の成否は別です。Action の成功は処理の成功であり、マージ条件の充足を意味しません。PR ごとの結果は Job Summary と artifact で確認してください。

## CI・PR イベントも処理する

[完全な workflow 例](examples/pr-merge-readiness.yml)には、CI 開始・完了、PR 状態変更、設定・workflow の変更を起動条件として含めています。`on.workflow_run.workflows` と TOML の `ci.workflows` を合わせてください。

`pull_request` は PR head、`push` は push 対象 SHA の設定を検証して終了します。運用イベントでは default branch の設定 SHA を一度確定し、最後まで同じ設定を使用します。公開直前にも head/base・状態を確認し、遅延結果で新しい表示を戻しません。

自動入口の job は、観測と公開に必要な権限をまとめて持ちます。設定検証時も同じ権限設定ですが、検証経路は設定の読み取りだけで終了し、観測・公開へ進みません。PR のソースコードを checkout・実行しません。fork PR で token が読み取り専用になっても設定検証は可能です。

同じ Check を更新する job の concurrency group は `autonomous-merge-check-writer` に統一します。自動入口では設定検証・観測・保存・公開を含む job 全体が直列化されます。`cancel-in-progress: false` は実行中の job の自動キャンセルを防ぎますが、GitHub の既定の待機枠では新しい job が以前の待機中 job を置き換える場合があります。

[イベント対応・更新・個別 operation の使い方](docs/workflow.md)も参照してください。再利用可能 workflow と workflow 生成器は提供しません。

## 設定 version 1

```toml
version = 1
action_ref = "21fa2df95fc615847ba7e1a24e9c77bd5d1323fb" # v0.4.0

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
| `operation` | 既定 `run`。個別に `prepare`／`validate-config`／`observe`／`mark`／`publish-checks`／`publish-labels` も指定可能 |
| `action-ref` | 省略時は実際の remote Action 参照を使用。40 桁 SHA が必須。明示入力も実ソースと照合。local Action は明示指定 |
| `config-path` | 既定 `.github/pr-merge-readiness.toml` |
| `repository` | workflow の repository と一致すること。既定 `github.repository` |
| `token` | 必要な権限を持つ token。既定 `github.token` |
| `config-sha` | 個別 operation 用。公開・`validate-config` では必須、`prepare` では禁止 |
| `pr-number` | 個別の手動 `observe` 用の正整数 |
| `event-path` | 個別 `observe`／`mark` 用。省略時 `GITHUB_EVENT_PATH` |
| `report-dir`・`artifact-name` | 個別の観測・公開で必須。`mark` では禁止 |

`run` は `config-sha`・`pr-number`・`event-path`・`report-dir`・`artifact-name` の入力を受け取りません。手動入力は実イベントから取得し、設定 SHA と保存先を内部で決定します。観測レポートは runner の一時ディレクトリ内に保存し、同じ run ID・attempt の artifact として公開前にアップロードします。

`run` の出力 `operation` は `validate-config`／`mark`／`observe`／`skip`。設定を読んだ場合は `config-sha`、運用準備ではさらに `checks`・`labels`・`pr-number`、観測を選んだ場合は `report-dir`・`manifest`・`artifact-name` を出力します。`checks` と `labels` は `"true"`／`"false"` の文字列で、設定と選択を表します。公開成功の保証や複数 PR の判定を集約した boolean ではありません。

既存の個別 operation は入出力と動作を維持します。`prepare` と `validate-config` は PR・イベント・レポート入力を受け取らず、`publish-*` は PR 番号・イベントパスを受け取りません。個別利用時は利用側が設定 SHA、artifact 保存、権限、公開順序を管理します。

| operation | job の token 権限 |
| --- | --- |
| `run` | contents・actions・statuses の read、checks・pull-requests・issues の write |
| `prepare`・`validate-config` | `contents: read` |
| `observe` | contents・pull-requests・checks・statuses の read |
| `mark` | contents・pull-requests・actions の read、checks の write |
| `publish-checks` | contents・pull-requests の read、checks の write |
| `publish-labels` | contents の read、pull-requests・issues の write |

Action は固定版の uv で Python 3.14 を用意し、自身の `run.sh` から `python -I -B` で起動します。利用側の `pyproject.toml`、`uv.toml`、`.python-version`、仮想環境、Python モジュール、`PYTHONPATH`、`sitecustomize`、PR ソースを実行時に参照しません。入力値は環境変数を経由し、シェルコードに直接展開しません。

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
