# 運用と移行

この文書は公開済み v0.6.0 と現在の実装の動作を説明します。今後の責務は[Action の責務](responsibilities.md)、GitHub 側で担保する条件は[推奨 Ruleset](rulesets.md)を参照してください。

## 実行対象

| イベント | 処理 |
| --- | --- |
| PR の作成・再オープン・追加 push・Draft 切替・base 編集 | 提案設定を検証し、default branch の設定でその PR を判定・ラベル更新 |
| PR 終了 | 対象 PR を判定し、管理ラベルを除去 |
| タイトル・本文だけの編集 | 省略 |
| Run workflow：PR 番号指定 | その PR を判定・ラベル更新 |
| Run workflow：番号なし | 全 open PR を判定・ラベル更新し、終了済み PR の管理ラベルも除去 |
| push | push 先の SHA の設定を検証 |
| その他 | 省略 |

同一リポジトリの通常 PR だけを自動処理します。fork・Dependabot・作成元リポジトリが削除された PR は自動処理を省略します。手動同期ではこれらも観測しますが、管理ラベルは除去します。

現行版では、レビュー・スレッド解決・base branch の更新後は Run workflow で再評価できます。方針上、一般のレビュー会話の解決は Ruleset の担当です。履歴に基づく独自条件に必要な人間の承認や、base の更新を反映するときは、この Action の再観測が必要です。競合中の PR は `pull_request` イベントが起動しません。[GitHub のイベント仕様](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)を参照してください。

## 設定と公開

PR のソースコードは checkout・実行しません。PR イベントの head で設定を検証した後、default branch の設定コミットを一度だけ確定して判定します。提案が不正な場合はそこで失敗します。

全対象の観測が完了してからサマリーとラベルを更新します。PR イベントの head が待機中・観測中に変わった場合は公開を止め、公開直前の head が変わった場合もラベル更新を省略します。レビュー対象の base が変わっていれば再観測ラベルにします。

観測前後の PR 更新時刻（`updated_at`）だけの変化はサマリーに残しますが、情報不足や Action 失敗の理由にはしません。head・base・PR 状態・レビュー・未解決スレッドなどの判定情報は、引き続き観測前後の一致を確認します。

履歴取得は1 PR あたり最大100ファイル、run 全体で100要求です。同じ base SHA・パスの取得結果は run 内で共有し、失敗要求も上限に含めます。上限超過や情報不足を条件充足とは扱いません。

同じリポジトリのラベル更新 job は concurrency group を `autonomous-merge-check-writer` に統一します。旧版と同じ group を維持することで移行時も更新を直列化します。`cancel-in-progress: false`・`queue: max` を使用し、待機枠が満杯で要求がキャンセルされた場合は再実行してください。

## version 2 からの移行

v0.6.0 は互換性のない簡素化を含みます。v0.5.1 は version 3 を扱えないため、設定と Action の参照先を同時に更新します。

1. TOML の `version` を `3` にし、`[publication]` 全体と `action_ref` を削除します。`[review]` の3項目はそのまま使えます。
2. workflow を[新しい例](../examples/pr-merge-readiness.yml)に合わせます。v0.6.0 の配布用 SHA は `568c7441e16afa46db11bc84f1e4708e6525a404` です。
3. `checks: write` 権限と手動入力 `update-labels` を削除します。実行した対象のラベルは常に更新されます。旧版の `labels = "manual"`・`"off"` に相当する観測専用モードはありません。
4. 個別 operation や artifact 受け渡しの job を使っていた場合は、1 job・1 step の呼び出しに置き換えます。
5. 設定と workflow を同じコミットで default branch に反映し、Run workflow で PR 番号を空欄にしてラベルを同期します。

移行 PR では、旧版の Action は提案された version 3 を拒否し、新版の Action は default branch に残る version 2 を拒否します。移行完了前に新旧両方を同じ設定で成功させることはできません。これが必須チェックに組み込まれている利用先では、管理者の通常の変更手順で移行を調整してください。default branch に反映した後、新しい workflow を手動実行して確認します。

削除したもの：

- Action 入力の `operation`、`action-ref`、`repository`、`config-sha`、`pr-number`、`event-path`、`report-dir`、`artifact-name`
- local Action（`uses: ./path`）の呼び出し。提供元の remote Action を、上記の配布用 SHA に固定して指定します。
- 出力の `checks`、`labels`、`pr-number`、`report-dir`、`manifest`、`artifact-name`
- 参考 Check の公開、JSON artifact と manifest、CLI の `replay`
- 公開モードの設定と `update-labels` 入力

手動実行の `pr-number` は workflow の入力として残ります。Action の入力とは異なります。過去の Check は履歴として残り、新版は更新しません。過去 artifact の再評価が必要な場合は、記録と一致する信頼済みの旧版を使用してください。

このリポジトリ自身の `.github/` も v0.6.0 と version 3 を使用します。
