# 1回の Action 呼出しで処理する

[完全な workflow 例](../examples/pr-merge-readiness.yml)と[最小設定](../examples/minimal.toml)を利用側の `.github` にコピーします。設定の `action_ref` と workflow の `uses:` を同じ40桁 SHA に固定し、CI workflow 名と Check の名前・発行元を合わせてください。

通常の workflow から Composite Action を1回呼び出します。呼び出し側は起動条件、手動入力、runner、timeout、権限、concurrency を管理し、処理の分岐・順序・レポート保存を Action に任せます。checkout、再利用可能 workflow、生成コマンドは不要です。

## イベント

| イベント | Action 内の動作 |
| --- | --- |
| 設定・workflow の `pull_request` | PR head の TOML を検証して終了 |
| 設定・workflow の `push` | push 対象 SHA の TOML を検証して終了 |
| 対象 CI 開始（`workflow_run: in_progress`） | 未観測表示 |
| PR の opened／reopened／synchronize／ready_for_review／converted_to_draft、base の編集 | 未観測表示 |
| 対象 CI 完了（`workflow_run: completed`） | 全 open PR を観測 → 保存 → Check |
| PR 終了（`pull_request_target: closed`） | 当該 PR を観測 → 保存 → Check |
| Run workflow、PR 番号指定 | 指定 PR を観測 → 保存 → Check |
| Run workflow、番号なし | 全 open PR を観測 → 保存 → Check |
| Run workflow、`update-labels = true` | 全 open PR を観測 → 保存 → Check → ラベル |

PR 状態変更には `pull_request_target` を使います。タイトル・本文だけの編集は処理を省略します。承認イベントと日次実行は起動条件にしません。手動ラベル更新と PR 番号指定は併用できません。Check が無効ならその公開段階を省略します。`on.workflow_run.workflows` と TOML の `ci.workflows` は同じ名前にします。

## 設定と権限

運用時は default branch の設定 SHA を一度確定し、後続処理で同じ設定を使います。途中で default branch が進んでも再解決しません。設定・レポートの出所と Action の実ソースの SHA を照合します。`pull_request`・`push` の検証対象設定からは観測・公開へ進まず、PR のソースコードも実行しません。

1 job は contents・actions・statuses の read と、checks・pull-requests・issues の write を持ちます。設定検証時も job の権限設定は共通ですが、検証コードは読み取りだけで動作します。fork PR の読み取り専用 token でも検証できます。

Check writer の concurrency group は `autonomous-merge-check-writer`、`cancel-in-progress` は `false` に統一します。設定検証から公開まで job 全体を直列化し、ラベル用の別 group は不要です。GitHub の既定の待機枠では、新しい job が以前の待機中 job を置き換える場合があります。

## 保存と失敗

Action が今回の観測を `pr-merge-readiness-RUN_ID-ATTEMPT` に30日保存します。公開はアップロード成功後に同じ job のレポートを読み、manifest・全レポートの出所と鮮度を確認してから実行します。別 run・attempt の artifact を探す fallback はありません。

- 準備失敗：`preparation-report/` の診断を専用 artifact に保存し、後続処理を止めます。
- 観測失敗：今回のレポートを保存します。有効な manifest がある場合は公開を試みますが、Action 全体の失敗は保持します。収集全体の失敗・不正な manifest は公開を拒否します。
- artifact 保存失敗：Check・ラベルを公開しません。
- Check 公開失敗：ラベルを更新しません。
- キャンセル：後続の公開を開始しません。

同じ run の再実行では、前 attempt の artifact が取得できなくなる場合があります。再観測には新しい Run workflow を使い、必要な証跡は再実行前に Git 外へ保存してください。

## 個別 operation と更新

`operation` を明示すると、従来の `prepare`・`validate-config`・`observe`・`mark`・`publish-checks`・`publish-labels` を個別に利用できます。read-only の観測だけを行う用途などで使います。個別利用では呼び出し側が同じ設定 SHA の引き継ぎ、artifact 保存、公開順序、限定権限を管理します。自動保存は既定の `run` だけが行います。

ローカルで TOML を検証する場合は、固定版 Action の CLI を使用できます。

```sh
uv python install --no-config 3.14
bash /absolute/path/to/pr-merge-readiness-action/run.sh validate-config \
  --config .github/pr-merge-readiness.toml
```

Action 更新時は TOML の `action_ref` と workflow の `uses:` を同じ公開済み SHA にまとめて変更します。明示した `action-ref` 入力がある場合はそれも更新します。CI workflow 名を変える場合は TOML と `on.workflow_run.workflows` を合わせます。

同じ Check・ラベルを更新する既存 writer から切り替える場合は、実行終了を確認してから入口を一つの変更で切り替えます。切り戻しは workflow と TOML を同時に revert します。判定・レポート形式は変わらず、artifact の変換も行いません。
