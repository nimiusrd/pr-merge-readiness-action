# 通常の workflow から利用する

[完全な workflow 例](../examples/pr-merge-readiness.yml)を利用側の `.github/workflows/pr-merge-readiness.yml` に、[最小設定](../examples/minimal.toml)を `.github/pr-merge-readiness.toml` にコピーします。

設定の `action_ref` と全 step の `uses:`・`action-ref` を同じ release の 40 桁 SHA に置き換えてください。CI workflow 名と Check の名前・発行元は自分の構成に合わせます。`on.workflow_run.workflows` と TOML の `ci.workflows` には同じ CI workflow 名を指定してください。

設定と workflow は利用側で編集・管理します。Python 実装は Action 側で管理し、利用側の checkout は必要ありません。再利用可能 workflow の呼出しや生成コマンドは使いません。

## job と設定の固定

| job / operation | 役割 |
| --- | --- |
| `validate` / `validate-config` | PR head または push の commit にある提案中の TOML を read-only で検証 |
| `prepare` | default branch の設定 SHA を確定し、元のイベントから後続処理を選択 |
| `mark` | CI 開始・PR 状態変更を未観測として表示 |
| `observe` | PR ごとの観測・判定・Summary を保存 |
| `publish-checks` | 同じ run / attempt の artifact を参考 Check として公開 |
| `publish-labels` | 手動指定された全 open PR のラベルを更新 |

準備は設定を変更せず、`config-sha`、`operation`、`checks`、`labels`、`pr-number` を出力します。`checks` と `labels` は文字列で判定してください。後続 job にはこの設定 SHA を渡し、default branch が途中で進んでも同じ設定を使用します。Action の実ソース・入力 `action-ref`・設定 `action_ref` の一致も検証します。

観測は read-only、Check とラベルは別 job の限定権限です。Check が有効なら公開成功後にだけラベルを更新します。Check writer の concurrency group は `autonomous-merge-check-writer`、手動ラベル更新全体は `autonomous-merge-labels` です。

準備失敗時は `preparation-report/` に今回のエラー JSON・manifest・Summary を保存します。完全な例はこれを専用 artifact に保存します。信頼できる設定を確定できなかった場合、後続の公開 job は実行しません。

## イベント

| イベント | 動作 |
| --- | --- |
| 対象 CI 開始（`workflow_run: in_progress`） | `mark` |
| PR の opened／reopened／synchronize／ready_for_review／converted_to_draft、base の編集 | `mark` |
| 対象 CI 完了（`workflow_run: completed`） | 全 open PR を `observe` → Check |
| PR 終了（`pull_request_target: closed`） | 当該 PR を `observe` → Check |
| Run workflow、PR 番号指定 | 指定 PR を `observe` → Check |
| Run workflow、番号なし | 全 open PR を `observe` → Check |
| Run workflow、`update-labels = true` | 全 open PR を `observe` → Check → ラベル |
| 設定・workflow の pull_request／push | 提案中の設定を `validate-config` |

PR 状態変更には `pull_request_target` を使います。タイトル・本文だけの編集は準備で `skip` になります。承認イベントと日次実行は起動条件にしません。手動ラベル更新と PR 番号指定は併用できません。Check が無効ならその公開段階を省略します。

## 設定変更・更新

TOML と workflow を直接編集し、必要な変更を同じ PR にまとめます。Action 更新時には設定と全 step の固定 SHA、CI workflow 名の変更時には設定と `on.workflow_run.workflows` を合わせて更新してください。

完全な例の `validate` job は、提案中の commit を `config-sha` として指定し、TOML の構造・型・固定 SHA を検証します。提案中のソースコードを実行せず、運用 job が使う設定 SHA の確定とは分離します。workflow 自体の構文・job の条件・権限は PR のレビューで確認してください。

ローカルで設定を検証する場合は、固定版 Action の CLI を使用できます。

```sh
uv python install --no-config 3.14
bash /absolute/path/to/pr-merge-readiness-action/run.sh validate-config \
  --config .github/pr-merge-readiness.toml
```

同じ Check・ラベルを更新する既存 writer から切り替える場合は、実行終了を確認してから入口の切替を一つの変更にまとめます。ロールバックは切替変更の revert で行います。artifact 形式の変換は行いません。
