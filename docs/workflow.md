# 運用と移行

この文書は設定 version 4 に対応するソースの動作を説明します。公開済み v0.6.0 は version 3 を使用します。責務は[Action の責務](responsibilities.md)、GitHub 側で担保する条件は[推奨 Ruleset](rulesets.md)を参照してください。

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

一般のレビュー会話の解決は Ruleset の担当です。履歴に基づく独自条件に必要な人間の承認や、base の更新を反映するときは、この Action の再観測が必要です。時間経過で履歴の閾値を超えた場合も次回観測で評価します。レビュー投稿・base branch の更新・時間経過による自動の再観測は行いません。競合中の PR は `pull_request` イベントが起動しません。[GitHub のイベント仕様](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)を参照してください。

## 設定と公開

PR のソースコードは checkout・実行しません。PR イベントの head で設定を検証した後、default branch の設定コミットを一度だけ確定して判定します。提案が不正な場合はそこで失敗します。

全対象の観測が完了してからサマリーとラベルを更新します。PR イベントの head が待機中・観測中に変わった場合は公開を止め、公開直前の head が変わった場合もラベル更新を省略します。レビュー対象の base が変わっていれば再観測ラベルにします。

観測前後の PR 更新時刻（`updated_at`）だけの変化はサマリーに残しますが、情報不足や Action 失敗の理由にはしません。head・base・変更量と、履歴条件が要求する人間の承認情報は、観測前後の一致を確認します。Draft・競合・GitHub のレビュー判定・未解決スレッドは取得しません。履歴条件が承認を要求しない PR ではレビュー一覧も取得しません。

履歴取得は1 PR あたり最大100ファイル、run 全体で100要求です。同じ base SHA・パスの取得結果は run 内で共有し、失敗要求も上限に含めます。上限超過や情報不足を条件充足とは扱いません。

同じリポジトリのラベル更新 job は concurrency group を `autonomous-merge-check-writer` に統一します。旧版と同じ group を維持することで移行時も更新を直列化します。`cancel-in-progress: false`・`queue: max` を使用し、待機枠が満杯で要求がキャンセルされた場合は再実行してください。

## version 3 から version 4 への移行

このソースでは `minimum_approvals`・`require_resolved_threads` と、それに対応する判定を削除しました。version 3 や削除済みキーは受け付けません。公開済み v0.6.0 は version 4 に対応していないため、対応リリースの公開を待って移行します。

1. [推奨 Ruleset](rulesets.md)を参考に、必要な承認数・会話解決・CI・CODEOWNERS のルールを GitHub 側で管理します。
2. TOML の `version` を `4` にし、`[review]` の `minimum_approvals` と `require_resolved_threads` を削除します。`stale_change_review_days` はそのまま使います。
3. workflow の `uses:` を version 4 対応リリースが指す40桁 SHA に変更します。例の `<RELEASE_COMMIT_SHA>` は、この公開後に置き換えるプレースホルダーです。
4. 設定と workflow を同じコミットで default branch に反映し、Run workflow でラベルを同期します。旧 `shadow/レビュー待ち` も除去されます。

移行中は、旧 Action が提案された version 4 を拒否し、新 Action は default branch に残る version 3 を拒否します。両方の設定を受け付ける互換処理はありません。default branch に設定と参照先を揃えた後、手動実行で確認してください。マージ条件の変更や bypass を Action が行うことはありません。

本リポジトリ自身の `.github/` は、対応版の公開まで v0.6.0・version 3 を維持します。ソース CI は version 4 の例・テストを検証し、運用中の設定は workflow に固定した配布版が検証します。ローカルでビルドした `dist/` の変更は実装 PR に含めません。公開と `.github/` の切り替えは[リリース手順](releases.md)に従います。

## version 2 からの移行

以下は v0.6.0（version 3）へ移行する場合の手順です。version 4 を利用する場合は、上の手順も適用してください。

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
