# 共通 workflow と生成 CLI（任意）

ルートの [Composite Action](../README.md) は自分の workflow の step から直接利用できます。共通 workflow は、CI・PR イベントへの追従、未観測表示、観測、Check、手動ラベル公開、設定検証をまとめて導入したい場合の選択肢です。

## 導入

1. [最小設定](../examples/minimal.toml)または[複数 workflow の設定例](../examples/multiple-workflows.toml)を自分のリポジトリの `.github/pr-merge-readiness.toml` にコピーします。Action の release SHA、CI workflow 名、必須 Check の名前と発行元を設定してください。
2. 設定の `action_ref` と同じ 40 桁 SHA の Action ソースを取得します。CLI の実行には uv と Python 3.14 を使用します。
3. 利用側リポジトリのルートで、取得したソースの CLI から起動用 workflow を生成します。

```sh
uv python install --no-config 3.14
bash /absolute/path/to/pr-merge-readiness-action/run.sh generate-workflow \
  --config .github/pr-merge-readiness.toml \
  --output .github/workflows/pr-merge-readiness.yml
```

この方法では、利用側が管理するのは設定、生成した入口、利用案内だけです。job 定義と Python 実装は Action リポジトリで管理します。生成物を手編集せず、設定変更後に CLI を再実行してください。`--check` は書き換えず、差分または欠落で終了コード 1 を返します。

設定変更の PR では、生成した入口から read-only の検証 workflow を呼び、提案中の設定と生成物を検証します。運用時は default branch の信頼済み設定を読みます。

## イベント

| イベント | 動作 |
| --- | --- |
| 対象 CI の開始（`workflow_run: in_progress`） | `mark` で未観測 Check |
| PR の opened／reopened／synchronize／ready_for_review／converted_to_draft、base の編集 | `mark` で未観測 Check |
| 対象 CI の完了（`workflow_run: completed`） | 全 open PR を `observe` → Check 公開 |
| PR 終了（`pull_request_target: closed`） | 当該 PR を `observe` → Check 公開 |
| Run workflow、PR 番号指定 | 指定 PR を `observe` → Check 公開 |
| Run workflow、番号なし | 全 open PR を `observe` → Check 公開 |
| Run workflow、`update-labels = true` | 全 open PR を `observe` → Check → ラベル公開 |

PR イベントは `pull_request_target` を使います。タイトル・本文だけの編集、承認イベント、日次実行は起動条件にしません。手動ラベル更新と PR 番号指定は併用できません。Check が無効なら公開段階を省略します。Check 公開が失敗した場合、ラベル公開には進みません。

共通 workflow の入力は `config-path`、`action-ref`、`pr-number`、`update-labels` です。元のイベント payload を利用し、準備 job が後続 job を選択します。

## 設定と権限

準備 job が default branch の設定 SHA を一度確定し、全 job が同じ設定を使用します。共通 workflow の実 SHA、Action の実ソース SHA、入力 `action-ref`、設定 `action_ref` の一致を検証します。

観測は read-only、Check とラベルは別 job の限定権限で公開します。Check writer の concurrency group は `autonomous-merge-check-writer`、手動ラベル更新全体は `autonomous-merge-labels` です。

## 別の writer から切り替える場合

同じ Check・ラベルを更新する既存 writer があれば、実行終了を確認し、新しい入口への切替と既存入口の停止を同じ変更にまとめます。既存実装の削除は実測検証後に行います。ロールバックは切替・削除の変更を revert し、実装の復元と入口の切替をまとめます。artifact 形式の変換は行いません。
