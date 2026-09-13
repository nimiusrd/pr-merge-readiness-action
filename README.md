# PR Merge Readiness

GitHub の PR・レビュー・CI・変更履歴を読み取り、マージ準備状況を観測する独立 Action です。参考 Check と手動ラベルを公開できます。マージや承認、branch protection の変更は行いません。

対応環境は GitHub.com、Ubuntu、Python 3.14、Git です。Python は uv 0.12.13 で管理し、Action・CI・Dev Container・共通 workflow で同じ minor を使用します。実行時の Python 依存パッケージはありません。

## 導入

1. 検証済み release の **40 桁 commit SHA** を決め、対応するソースを取得します。タグやブランチ名を運用参照には使いません。
2. [設定例](examples/)を利用側の `.github/pr-merge-readiness.toml` にコピーし、`action_ref`、CI workflow 名、必須 Check の名前と発行元を設定します。
3. 利用側リポジトリのルートで、取得した Action の CLI から入口を生成します。

```sh
uv python install --no-config 3.14
bash /absolute/path/to/pr-merge-readiness-action/run.sh generate-workflow \
  --config .github/pr-merge-readiness.toml \
  --output .github/workflows/pr-merge-readiness.yml
```

利用側で管理するのは設定、生成した入口、短い利用案内だけです。job 定義・Python 実装はこのリポジトリにあります。生成物を手編集せず、設定変更後に同じ CLI を再実行してください。`--check` は書き換えず、差分または欠落で終了コード 1 を返します。提案中の設定と生成物は、入口が呼ぶ read-only の共通検証 workflow で検証します。

## 設定 version 1

```toml
version = 1
action_ref = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" # 実際の release SHA に置換

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

変更された既存ファイルの base 上の最終変更から、観測時点で **30 日を超える**場合、現在 head に対する人間の承認を要求します。ちょうど 30 日では発動しません。承認者は User かつ OWNER／MEMBER／COLLABORATOR、レビューは現在 head の有効な APPROVED である必要があります。Bot、外部ユーザー、旧 head、dismiss されたレビューは解除条件になりません。追加ファイルは過去履歴を持たず、rename は旧パスの履歴を使います。

履歴 API は 1 PR あたり最大 100 ファイル、run 全体で最大 100 要求です。失敗要求も消費し、同じ base SHA・パスは PR 間で cache を共有します。別 base、別 run に cache は引き継ぎません。上限超過・API 失敗・履歴不正は情報不足として扱います。

## イベント・公開

| イベント | 動作 |
| --- | --- |
| 対象 CI 開始、PR の head/base・Draft 等の状態変更 | 未観測 Check |
| 対象 CI 完了 | 全 open PR を観測して Check を公開 |
| PR 終了 | 当該 PR を観測して Check を公開 |
| Run workflow、PR 番号指定 | 指定 PR を観測して Check を公開 |
| Run workflow、番号なし | 全 open PR を観測して Check を公開 |
| Run workflow、`update-labels = true` | 全 open PR を観測 → Check → ラベル公開 |

タイトル・本文だけの編集、承認イベント、日次実行は起動条件にしません。手動ラベル更新と PR 番号指定は併用できません。Check が無効なら、その公開段階だけを省略します。Check 公開が失敗した場合、ラベル公開には進みません。

判定は `SHADOW_CONDITIONS_MET`、`WAITING`、`HUMAN_REVIEW_REQUIRED`、`INSUFFICIENT_DATA` の 4 種類です。表示名は移植元と同じ `Autonomous Merge Shadow / PR #番号` と管理ラベルを使い、Check conclusion は常に `neutral` です。利用者が必須 Check として登録する用途ではありません。

準備 job が default branch の設定 SHA を一度確定します。後続 job はその設定だけを読み、共通 workflow の実 SHA、Action の実ソース、入力 `action-ref`、設定の `action_ref` を照合します。観測は read-only、Check とラベルは別 job の限定権限で公開します。公開直前に現在の head/base・状態を確認し、遅延結果で新しい表示を戻しません。Check writer の concurrency は `autonomous-merge-check-writer`、手動ラベル全体は `autonomous-merge-labels` です。

## Composite Action の入力・出力

通常は生成入口から共通 workflow を呼んでください。個別 job を組む場合のルート Action の契約は次のとおりです。

| 入力 | 契約 |
| --- | --- |
| `operation` | 必須。`observe`／`mark`／`publish-checks`／`publish-labels` |
| `action-ref` | 必須。Action 自身の 40 桁 SHA |
| `config-path` | default branch の設定パス。既定 `.github/pr-merge-readiness.toml` |
| `config-sha` | 固定した設定 commit。公開では必須。省略時は default branch を解決 |
| `repository` | workflow の repository と一致すること。既定 `github.repository` |
| `pr-number` | 手動 `observe` だけで使う任意の正整数 |
| `event-path` | `observe`／`mark` 用。省略時 `GITHUB_EVENT_PATH` |
| `report-dir` | 観測・公開で必須。`mark` では禁止 |
| `artifact-name` | 観測・公開で必須。`pr-merge-readiness-RUN_ID-ATTEMPT`。`mark` では禁止 |
| `token` | 必須の権限を持つ token。既定 `github.token` |

`publish-*` は `pr-number` と `event-path` を受け取りません。`publish-labels` は実イベントの明示的な手動入力と、全 open PR 観測を要求します。出力は `report-dir`、`manifest`、`artifact-name`、`config-sha`。`mark` の出力は設定 SHA だけです。複数 PR の判定を boolean に集約しません。

Action は固定版の uv で Python 3.14 を用意し、自身の `run.sh` から `python -I -B` で起動します。起動時は利用側の `pyproject.toml`、`uv.toml`、`.python-version`、仮想環境を参照しません。利用側の Python モジュール、`PYTHONPATH`、`sitecustomize`、PR ソースを実行しません。入力値は環境変数を経由し、シェルコードに直接展開しません。

## レポートとオフライン再評価

artifact 名は run ID と attempt ごとに分け、保持期間を 30 日に設定します。PR 別 `pr-番号.json`、`manifest.json`、`summary.md` を保存し、収集失敗時にも今回の JSON と Summary を残します。別 run／attempt の artifact を探す fallback はありません。

同じ run の全 job 再実行では、前 attempt の artifact が GitHub API から取得できなくなる挙動を[実測](https://github.com/nimiusrd/nimius-player/actions/runs/34766780386/attempts/2)しました。名前を attempt ごとに分けても発生し、[upload-artifact #585](https://github.com/actions/upload-artifact/issues/585) にも同種の報告があります。再観測には新しい **Run workflow** を使い、再実行する場合は必要な artifact を先に Git 外へ保存してください。30日の保存設定は、GitHub 上で削除・再実行された artifact の再取得を保証しません。

レポートは `format = "pr-merge-readiness/report"`、manifest は `format = "pr-merge-readiness/manifest"`、いずれも `schema_version = 1` です。レポートには観測、正規化 policy、policy の SHA-256 指紋、判定、conditions、出所を保存します。出所は評価器・設定・共通 workflow の repository／SHA／path。共通 workflow を使わない実行の workflow 出所は null です。manifest は repository、run ID、attempt、artifact 名、観測範囲、PR 別ファイル一覧、収集失敗状態、同じ出所を持ちます。公開前に一覧の全ファイルを検証します。

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

## 開発・移行

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

移行は旧 writer の実行終了を確認し、新入口の追加と旧入口の停止を同じ PR にまとめます。旧実装は実測検証後の整理 PR で削除します。削除後のロールバックは、整理変更と移行変更の両方を revert した 1 本の PR で旧実装の復元と入口の切替をまとめます。新旧 artifact は変換しません。

実測記録には run URL、attempt、対象 SHA、Action SHA、設定 SHA、JSON 名、期待値・実測値を残します。生 JSON は artifact または Git 外に保存します。本人名義の検証 PR では自己承認を使えないため、人間承認による解除は自動テストで確認し、実測済みとは記載しません。

## ライセンス・移植元

MIT License、Copyright (c) 2026 Yudai Udagawa。[LICENSE](LICENSE) の著作権表示を保持しています。収集・評価・公開の振る舞いとテストケースは [devops-tycoon の実装](https://github.com/nimiusrd/devops-tycoon/tree/3f001201b2a4051180c7d10e04afcd59166ef7c3/.github/autonomous-merge)を参考に、新しいパッケージ・設定・artifact 契約へ整理しました。計画と両リポジトリへの導入記録は [Issue #499](https://github.com/nimiusrd/devops-tycoon/issues/499) に集約します。
