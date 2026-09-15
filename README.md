# PR Merge Readiness

GitHub の PR・レビュー・変更履歴を読み取り、レビュー条件の充足と必要な対応を判定する Composite Action です。任意のリポジトリの workflow から `uses:` で呼び出し、PR ごとの JSON と Job Summary を生成できます。参考 Check とラベルの公開にも対応しています。

対応環境は GitHub.com、Ubuntu 22.04 以降の Linux x64 / arm64、Git です。Python プロジェクトは uv 0.12.13 で管理し、リリース時に PyInstaller で Python 3.14 同梱バイナリを生成します。Composite Action は同梱バイナリを起動するため、利用側での uv・Python の導入、依存解決、ビルドは不要です。

> `.github/` と利用例は [v0.4.0](https://github.com/nimiusrd/pr-merge-readiness-action/releases/tag/v0.4.0) のバイナリ同梱 SHA `107e80a91574e277ea3c13e41aeff7710cae77e2` に固定しています。[リリースと導入](docs/releases.md)に従い、`uses:` と `action_ref` を同じ配布用 SHA に揃えてください。今後のリリースは検証済みバイナリを main に反映してタグを付けます。利用側には、そのタグが指す40桁 SHA を指定します。

CI の待機・成功・失敗・再実行履歴は GitHub Checks に任せます。この Action は CI の結果、commit status、`mergeStateStatus` を収集・判定・レポート化しません。CI 定義ファイルの変更は、変更内容に対するレビュー条件として扱います。

> 設定は version 2、観測・レポートは schema version 2 です。version 1 の設定・レポートとは互換性がないため、既存の利用側は [移行手順](docs/workflow.md#公開後の移行)に従い Action SHA と設定を同時に切り替えてください。

> 自動観測と参考 Check は通常 PR が対象です。fork・Dependabot は対象外です。ラベルは既定で手動更新し、次回リリースから `labels = "auto"` で PR イベント時の自動更新も選べます。[完全な workflow 例](examples/pr-merge-readiness.yml)を導入するときは、起動条件・`uses:`・TOML の `action_ref` をまとめて更新してください。

## クイックスタート

1. [最小設定](examples/minimal.toml)を自分のリポジトリの `.github/pr-merge-readiness.toml` にコピーし、承認数と変更履歴のレビュー条件を合わせます。
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
      queue: max
    permissions:
      contents: read
      checks: write
      pull-requests: write
      issues: write
    steps:
      - uses: nimiusrd/pr-merge-readiness-action@107e80a91574e277ea3c13e41aeff7710cae77e2 # v0.4.0
```

`uses:` と設定の `action_ref` は同じ **40 桁 commit SHA** に固定します。この例は v0.4.0 の配布用コミット `107e80a91574e277ea3c13e41aeff7710cae77e2` を使用します。入力 `action-ref` は実際の参照から取得するため、省略できます。

既定の `operation: run` が、イベントと TOML の設定から設定検証・観測・Check・ラベル更新を選びます。呼び出し側は **1 job・1 step** で利用でき、`if`、`needs`、設定 SHA の受け渡し、artifact の upload/download を組み立てる必要はありません。実装の起動も Action 内で行い、利用側の checkout は不要です。

PR 番号が空なら全 open PR、指定するとその PR を観測します。手動実行のラベル更新は `update-labels = true` を明示した場合だけ実行し、PR 番号指定との併用は拒否します。`labels = "auto"` の場合も、手動実行ではこの入力に従います。Check が有効なら、観測 → artifact 保存 → Check → ラベルの順に進み、保存や Check 公開の失敗後はラベルを更新しません。

判定と観測の成否は別です。Action の成功は処理の成功であり、CI の成功やマージ条件全体の充足を意味しません。PR ごとの結果は Job Summary と artifact で確認してください。

## PR イベントも処理する

[完全な workflow 例](examples/pr-merge-readiness.yml)は `pull_request` で起動します。同一リポジトリから作成された、Dependabot 以外の PR が対象です。PR 作成・追加 push・Draft 切替・base 変更・終了時は、その PR を直接観測して Check を更新します。タイトル・本文だけの編集は処理を省略し、CI 開始・完了では起動しません。

対象の `pull_request` は PR head の設定を検証した後、default branch の設定を取得して観測・公開へ進みます。提案設定が不正な場合はそこで失敗し、観測しません。提案設定の policy は観測・公開に使いません。`push` は push 対象 SHA の設定を検証して終了します。運用時は default branch の設定 SHA を一度確定し、最後まで同じ設定を使用します。公開直前にも head/base・状態を確認し、遅延結果で新しい表示を戻しません。

自動観測は設定を検証したイベントの head SHA に固定します。待機中や観測中の追加 push で head が変わった場合は、診断を保存して判定・公開を止めます。観測後に head が変わった場合も Check 公開を省略し、新しい PR イベントで再評価します。

自動入口の job は、観測と公開に必要な権限をまとめて持ちます。PR のソースコードを checkout・実行しません。fork PR と Dependabot の PR は、実行者が人間の場合も自動処理を省略します。手動実行では観測レポートを保存できますが、これらの PR には参考 Check を作成・更新しません。ラベル同期では既存の管理ラベルを除去します。

`pull_request` は競合中の PR では起動しません。競合中の PR、レビュー・スレッド解決・base ブランチの更新を再評価するときは Run workflow を使います。[GitHub の起動条件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)を参照してください。

workflow を編集できる書き込み権限者は信頼対象です。GitHub ではこの権限者が `permissions` も編集できるため、Action の検証経路や同じ workflow 内の job 分離は、workflow 定義の改変を防ぐ境界にはなりません。外部 fork の `pull_request` には GitHub の読み取り専用 token 制限が適用されます。[GitHub の権限設定](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository)を参照してください。

同じ Check を更新する job の concurrency group は `autonomous-merge-check-writer` に統一します。自動入口では設定検証・観測・保存・公開を含む job 全体が直列化されます。`cancel-in-progress: false` と `queue: max` により、実行中の job を止めず、手動ラベル要求を含む最大100件の job を待機させます。後続イベントは既存の待機要求を置き換えません。待機枠が満杯の場合は追加の要求がキャンセルされるため、その手動要求は空きができてから再実行してください。[GitHub のキュー仕様](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)に従います。

[イベント対応・更新・個別 operation の使い方](docs/workflow.md)も参照してください。再利用可能 workflow と workflow 生成器は提供しません。

## 設定 version 2

```toml
version = 2
action_ref = "107e80a91574e277ea3c13e41aeff7710cae77e2"

[review]
minimum_approvals = 0
require_resolved_threads = true
stale_change_review_days = 30

[publication]
checks = true
labels = "manual"

```

`ci` と `required_checks` は設定に含めません。未知キー、不正型、非固定 SHA は拒否します。version 1 の設定は受理しません。

`review` の全項目は必須です。承認数は 0 以上、レビュー閾値は正の整数です。`publication` は省略でき、既定値は `checks = true`、`labels = "manual"` です。

| `publication.labels` | PR イベント | 手動実行 |
| --- | --- | --- |
| `"auto"`（次回リリース） | 対象 PR のラベルを更新 | `update-labels = true` で全 open PR を同期 |
| `"manual"`（既定） | ラベルを更新しない | `update-labels = true` で全 open PR を同期 |
| `"off"` | ラベルを更新しない | ラベル更新要求を拒否 |

### PR 作成・更新時の自動ラベル

**現在の v0.4.0 バイナリは `"auto"` に未対応です。** この変更を含むリリースの公開後、workflow の `uses:` と TOML の `action_ref` をその配布用 SHA に更新し、default branch の設定を次のように変更します。[移行手順](docs/workflow.md#自動ラベルへの移行)を参照してください。

```toml
[publication]
checks = true
labels = "auto"
```

[完全な workflow 例](examples/pr-merge-readiness.yml)の `pull_request` 起動条件で、PR 作成・再オープン・追加 push・base の編集・Draft 切替時に当該 PR のラベルを更新します。PR 終了時は当該 PR の管理ラベルを除去します。自動実行で他の PR を同期・清掃しません。タイトル・本文だけの編集やレビュー・CI のイベントでは更新しません。

公開前にイベント元 PR・観測レポートの PR 番号と head SHA を照合し、追加 push で head が変わっていればラベル更新を省略します。PR の提案設定では自動更新を有効化できず、運用には default branch で確定した設定を使います。

### 承認と変更履歴

[承認を必須にする設定例](examples/review-policy.toml)では、現在 head に対する承認を1件、変更履歴のレビュー閾値を14日に設定しています。

この設定例では、変更された既存ファイルの base 上の最終変更から、観測時点で **30 日を超える**場合、現在 head に対する人間の承認を要求します。ちょうど 30 日では発動しません。承認者は User かつ OWNER／MEMBER／COLLABORATOR、レビューは現在 head の有効な APPROVED である必要があります。Bot、外部ユーザー、旧 head、dismiss されたレビューは解除条件になりません。追加ファイルは過去履歴を持たず、rename は旧パスの履歴を使います。

履歴 API は 1 PR あたり最大 100 ファイル、run 全体で最大 100 要求です。失敗要求も消費し、同じ base SHA・パスは PR 間で cache を共有します。別 base、別 run に cache は引き継ぎません。上限超過・API 失敗・履歴不正は情報不足として扱います。

## 判定と表示

判定対象は PR の open・Draft・競合状態、GitHub のレビュー要求、現在 head への承認、変更要求、未解決スレッド、変更履歴、CI 定義ファイルの変更です。CI 成功や base への追随を含む総合マージ可否は、GitHub の表示とルールで確認してください。

ラベルはレビューと変更履歴の条件だけを表します。CI の実行状態と PR の open/closed・Draft・競合状態はラベルの判定に含めません。

| 表示 | ラベルの条件 |
| --- | --- |
| `shadow/レビュー条件充足` | レビュー・変更履歴の条件を満たす |
| `shadow/レビュー待ち` | 必要なレビュー承認を待つ |
| `shadow/要対応` | 変更要求・未解決スレッド・古いファイルの変更に必要な承認・CI 定義の変更などに対応が必要 |
| `shadow/再観測が必要` | レビュー対象やレビュー・変更履歴の情報が不足、または観測中／公開前にレビュー対象が変わった |

たとえば Draft や競合があっても、レビュー条件を満たせばラベルは `shadow/レビュー条件充足` です。参考 Check・レポートでは引き続き PR 状態も判定します。観測中に Draft・競合・open/closed・更新時刻だけが変わってもラベルは再観測扱いにしません。head・base・レビュー要求・レビュー内容・未解決スレッドの変化は、ラベルでも再観測が必要です。

手動同期時は、作成元とマージ先が同じリポジトリの open PR にラベルを付けます。fork PR、作成元リポジトリが削除された PR、Dependabot が作成した PR は付与対象から外し、残っている管理ラベルを除去します。Dependabot は PR 作成者が `dependabot[bot]` かで判定するため、人間による手動実行でも対象外です。closed／merged PR に残る管理ラベルも除去します。終了状態を表すラベルは付けません。次の手動同期で `shadow/CI・レビュー待ち` と `shadow/要マージ判断` の付与も取り除き、既存ラベルの説明を更新します。

判定は `SHADOW_CONDITIONS_MET`、`WAITING`、`HUMAN_REVIEW_REQUIRED`、`INSUFFICIENT_DATA` の 4 種類です。Check の表示名は `Autonomous Merge Shadow / PR #番号`、conclusion は常に `neutral` です。マージや承認、branch protection の変更は行わず、必須 Check として登録する用途ではありません。

## Composite Action の入力・出力

ルートの `action.yml` が提供するインターフェースです。

| 入力 | 契約 |
| --- | --- |
| `operation` | 既定 `run`。個別に `prepare`／`validate-config`／`observe`／`publish-checks`／`publish-labels` も指定可能 |
| `action-ref` | 省略時は実際の remote Action 参照を使用。40 桁 SHA が必須。明示入力も実ソースと照合。local Action は明示指定 |
| `config-path` | 既定 `.github/pr-merge-readiness.toml` |
| `repository` | workflow の repository と一致すること。既定 `github.repository` |
| `token` | 必要な権限を持つ token。既定 `github.token` |
| `config-sha` | 個別 operation 用。公開・`validate-config` では必須、`prepare` では禁止 |
| `pr-number` | 個別の手動 `observe` 用の正整数 |
| `event-path` | 個別 `observe` 用。省略時 `GITHUB_EVENT_PATH` |
| `report-dir`・`artifact-name` | 個別の観測・公開で必須 |

`run` は `config-sha`・`pr-number`・`event-path`・`report-dir`・`artifact-name` の入力を受け取りません。手動入力は実イベントから取得し、設定 SHA と保存先を内部で決定します。観測レポートは runner の一時ディレクトリ内に保存し、同じ run ID・attempt の artifact として公開前にアップロードします。

`run` の出力 `operation` は `validate-config`／`observe`／`skip`。設定を読んだ場合は `config-sha`、運用準備ではさらに `checks`・`labels`・`pr-number`、観測を選んだ場合は `report-dir`・`manifest`・`artifact-name` を出力します。`checks` と `labels` は `"true"`／`"false"` の文字列で、設定と選択を表します。公開成功の保証や複数 PR の判定を集約した boolean ではありません。

個別 operation でも同じ観測・公開処理を使います。`prepare` と `validate-config` は PR・イベント・レポート入力を受け取らず、`publish-*` は PR 番号・イベントパスを受け取りません。個別利用時は利用側が設定 SHA、artifact 保存、権限、公開順序を管理します。

| operation | job の token 権限 |
| --- | --- |
| `run` | contents の read、checks・pull-requests・issues の write |
| `prepare`・`validate-config` | `contents: read` |
| `observe` | contents・pull-requests の read |
| `publish-checks` | contents・pull-requests の read、checks の write |
| `publish-labels` | contents の read、pull-requests・issues の write |

Action は `run-binary.sh` で OS と CPU を確認し、自身の `dist/linux-x64/` または `dist/linux-arm64/` のバイナリを起動します。利用側の `pyproject.toml`、`uv.toml`、`.python-version`、仮想環境、Python モジュール、`PYTHONPATH`、`sitecustomize`、PR ソースを実行時に参照しません。入力値は環境変数を経由し、シェルコードに直接展開しません。バイナリは固定した Action コミットに含まれ、実行時に Release assets をダウンロードしません。未対応 OS・CPU、バイナリがない参照では明示的に失敗します。

## レポートとオフライン再評価

artifact 名は run ID と attempt ごとに分け、保持期間を 30 日に設定します。PR 別 `pr-番号.json`、`manifest.json`、`summary.md` を保存し、収集失敗時にも今回の JSON と Summary を残します。別 run／attempt の artifact を探す fallback はありません。

同じ run の全 job 再実行では、前 attempt の artifact が取得できなくなる場合があります。[upload-artifact #585](https://github.com/actions/upload-artifact/issues/585) に、名前を attempt ごとに分けた場合も含む報告があります。再観測には新しい **Run workflow** を使い、再実行する場合は必要な artifact を先に Git 外へ保存してください。30日の保存設定は、GitHub 上で削除・再実行された artifact の再取得を保証しません。

レポートは `format = "pr-merge-readiness/report"`、manifest は `format = "pr-merge-readiness/manifest"`、観測・レポートは `schema_version = 2`、manifest は `schema_version = 1` です。レポートには観測、正規化 policy、policy の SHA-256 指紋、判定、conditions、ラベル用の `label_assessment`（decision と conditions）、出所を保存します。`observations.stable` は PR 状態を含む全体の鮮度、`observations.review_stable` はレビュー対象・レビュー・変更履歴の判定に使う情報の鮮度を表します。Summary にも全体とラベル用の判定を表示します。出所は評価器・設定の repository／SHA／path です。`provenance.workflow` は常に null です。manifest は repository、run ID、attempt、artifact 名、観測範囲、PR 別ファイル一覧、収集失敗状態、同じ出所を持ちます。公開前に一覧の全ファイルを検証します。

新しい artifact を展開し、その Action SHA を含む、利用者が信頼したローカル Git リポジトリを指定します。

```sh
bash /absolute/path/to/release-checkout/run-binary.sh replay \
  --report /absolute/path/to/artifact/pr-123.json \
  --source-dir /absolute/path/to/trusted-action-git \
  --source-repository nimiusrd/pr-merge-readiness-action \
  --action-sha <信頼済み40桁SHA>
```

保存した出所と指定 SHA を照合し、`git archive` でその commit の評価器と依存モジュールを取り出します。同じバイナリを子プロセスとして起動し、通信を禁止して、取り出した評価器を専用 namespace に読み込みます。バイナリ内の現在の評価器に置き換えず、保存情報による判定全体を比較します。現在時刻、現在 checkout、ネットワークは使いません。結果が同じなら `matches: true`、終了コード 0 です。

CI 条件を含む旧 policy、schema version 1 のレポートは非対応です。旧 artifact の変換や旧形式への fallback はありません。過去の記録を再評価する場合は、その記録と一致する信頼済みの旧版 CLI を別途使用してください。

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

`.python-version` で Python 3.14、`uv.lock` で開発・ビルド依存関係を固定します。依存更新時は `uv lock --upgrade` で lockfile を更新し、上記検証を実行してください。テストは pytest の関数・fixture・パラメータ化で記述し、Ruff で lint と整形、mypy の strict mode でパッケージ・CLI・検証スクリプトを型検査します。

ソースから CLI を動かす開発用途では、従来どおり `bash run.sh <command>` が uv 管理の Python 3.14 を `-I -B` で起動します。Composite Action はこの開発用 launcher を呼びません。

バイナリのビルド・検証と公開は[リリース手順](docs/releases.md)を参照してください。通常の pytest ではバイナリ専用テストを skip し、CI の Linux x64 / arm64 の各 job ではビルドした実行ファイルを指定して実行します。

実測記録には run URL、attempt、対象 SHA、Action SHA、設定 SHA、JSON 名、期待値・実測値を残し、生 JSON は artifact または Git 外に保存します。

不具合や改善提案は[このリポジトリの Issues](https://github.com/nimiusrd/pr-merge-readiness-action/issues)で管理します。

## ライセンス

[MIT License](LICENSE)。著作権と出典は [NOTICE](NOTICE.md) に記載しています。
