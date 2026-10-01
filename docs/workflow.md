# 運用と移行

この文書は `with` 入力を使う開発ソースの動作を説明します。公開済み v0.7.0 は TOML 設定を使用します。責務は[Action の責務](responsibilities.md)、GitHub 側で担保する条件は[推奨 Ruleset](rulesets.md)を参照してください。

## 実行対象

| イベント | 処理 |
| --- | --- |
| PR の作成・再オープン・追加 push・Draft 切替・base 編集 | `with` の閾値でその PR を判定・ラベル更新 |
| PR 終了 | 対象 PR を判定し、管理ラベルを除去 |
| タイトル・本文だけの編集 | 省略 |
| Run workflow：PR 番号指定 | その PR を判定・ラベル更新 |
| Run workflow：番号なし | 全 open PR を判定・ラベル更新し、終了済み PR の管理ラベルも除去 |
| その他 | 省略 |

同一リポジトリの通常 PR だけを自動処理します。fork・Dependabot・作成元リポジトリが削除された PR は自動処理を省略します。手動同期ではこれらも観測しますが、管理ラベルは除去します。

一般のレビュー会話の解決は Ruleset の担当です。履歴に基づく独自条件に必要な人間の承認や、base の更新を反映するときは、この Action の再観測が必要です。時間経過で履歴の閾値を超えた場合も次回観測で評価します。レビュー投稿・base branch の更新・時間経過による自動の再観測は行いません。競合中の PR は `pull_request` イベントが起動しません。[GitHub のイベント仕様](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)を参照してください。

## 設定と公開

PR のソースコードは checkout・実行しません。実行中の workflow の `with.stale-change-review-days` を使い、入力が不正な場合は API 取得とラベル更新の前に失敗します。既定値は30日です。設定ファイルや設定コミットの取得は行いません。

全対象の観測が完了してからサマリーとラベルを更新します。PR イベントの head が待機中・観測中に変わった場合は公開を止め、公開直前の head が変わった場合もラベル更新を省略します。レビュー対象の base が変わっていれば再観測ラベルにします。

観測前後の PR 更新時刻（`updated_at`）だけの変化はサマリーに残しますが、情報不足や Action 失敗の理由にはしません。head・base・変更量と、履歴条件が要求する人間の承認情報は、観測前後の一致を確認します。Draft・競合・GitHub のレビュー判定・未解決スレッドは取得しません。履歴条件が承認を要求しない PR ではレビュー一覧も取得しません。

履歴取得は1 PR あたり最大100ファイル、run 全体で100要求です。同じ base SHA・パスの取得結果は run 内で共有し、失敗要求も上限に含めます。上限超過や情報不足を条件充足とは扱いません。

同じリポジトリのラベル更新 job は concurrency group を `autonomous-merge-check-writer` に統一します。旧版と同じ group を維持することで移行時も更新を直列化します。`cancel-in-progress: false`・`queue: max` を使用し、待機枠が満杯で要求がキャンセルされた場合は再実行してください。

## 設定ファイルから with への移行

1. 対応版の公開後、workflow の `uses:` をリリースタグが指す40桁 SHA に変更します。公開済み v0.7.0 は `with` の閾値を扱えません。
2. TOML の `review.stale_change_review_days` を Action step の `with.stale-change-review-days` に移します。省略時は30日です。
3. `config-path` 入力、設定ファイル、設定検証用の `push` トリガーを削除します。
4. `config-sha` 出力と `validate-config` operation を参照する処理を削除します。設定検証 CLI も廃止しました。
5. default branch に反映し、Run workflow でラベルを同期します。

[利用例](../examples/pr-merge-readiness.yml)は対応版の SHA を差し込む形式です。本リポジトリの運用 workflow と `.github/pr-merge-readiness.toml` は、公開済み v0.7.0 を動かすために維持しています。対応版の公開後、参照 SHA と `with` を同時に更新し、TOML と `push` トリガーを削除してください。実装 PR では `dist/` を更新しません。配布準備・公開は[リリース手順](releases.md)に従います。
