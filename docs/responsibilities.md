# Action の責務

## 基本方針

PR Merge Readiness は、GitHub の標準ルールでは表現できない追加確認事項を、PR の差分メタデータと変更履歴から提示する補助 Action です。マージ条件の強制と最新の充足状況の判断は GitHub Ruleset に委ねます。

GitHub Ruleset で扱える条件は、この Action の判定として重複実装しません。利用先でルールが未設定の場合も、Action に同等の判定を追加する理由にはしません。設定の選択は[推奨 Ruleset](rulesets.md)で支援します。

以下は実装・変更の判断基準です。設定 version 4 のソースはこの方針に従います。公開済み v0.6.0 との違いは [v0.6.0 からの変更](#v060-からの変更)を参照してください。

## 責務の境界

| 確認したいこと | 担当 |
| --- | --- |
| PR 経由の変更、必要承認数、変更要求、承認の鮮度 | GitHub Ruleset |
| レビュー会話の解決 | GitHub Ruleset |
| CI の成功、マージ前に要求するブランチの最新化 | GitHub Ruleset と GitHub Checks |
| CI 定義など特定パスへの変更に対する担当者の承認 | CODEOWNERS と GitHub Ruleset |
| Draft・競合など通常のマージ可否 | GitHub の PR 画面・マージ制約 |
| 変更対象ファイルの履歴に応じた追加確認 | この Action |
| 観測した head・base の一致、履歴の取得漏れ | この Action が自身の判定の有効性を確認する |

GitHub 側の機能については[利用できる Ruleset のルール](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)と[CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)を参照してください。

PR の open / closed や対象リポジトリの確認は、観測対象の選択・終了済みラベルの除去に必要です。このような処理上の確認と、GitHub のマージ条件の再判定を区別します。Ruleset の設定検査・変更・同期はこの Action の責務に含めません。

## この Action が提供する追加確認

現在の独自条件は、変更対象の既存ファイルが base 側で最後に変更されてから、設定した日数を超えているかという確認です。例えば30日を超えていれば、そのファイルを変更する PR に追加の人間レビューを求めます。PR を作成してからの日数や、既存の承認が古くなったかという GitHub の判定とは別の条件です。

この条件を実装するために、人間の承認が現在の head に対して存在するかを参照します。承認情報を読む目的は、この履歴に基づく追加確認の完了を確かめることです。全 PR の最低承認数を数え直すためではありません。

CI 定義の変更をパスだけで検出して一律に対応を要求する条件は、担当者レビューを GitHub 側に設定する方針に従って削除しました。ファイル履歴に基づく条件は、CI 定義を含めた既存ファイルに同じ基準で適用できます。

新しい判定を追加するときは、GitHub 標準機能で表現できるかを先に確認します。表現できる場合は推奨設定の文書を更新し、この Action には追加しません。コード本文の品質評価、自動マージ、自動承認、定期実行、必須 Check の登録は行いません。

## 表示と観測タイミング

ラベルとサマリーは、対象コミットと観測時刻における追加確認の状態を伝えます。「要対応事項なし」は Action の担当範囲で追加確認がないという意味に限定します。GitHub の全条件が成立したことや、AI レビューが完了したことを示す表示にはしません。

独自条件の入力が揃っていれば、CI や一般のレビュー完了を待つ必要はありません。独自条件の入力が変わった場合には、再観測が必要です。

| 変化 | この Action での扱い |
| --- | --- |
| PR の head・base・変更ファイルが変わる | 新しい対象に対して再評価する |
| 履歴に基づき要求した人間の承認が投稿・取り消しされる | 追加確認の状態を再評価する |
| 時間経過でファイルの最終変更からの日数が閾値を超える | 次回観測時の時刻で再評価する |
| CI が完了する、一般のレビュー会話が解決される | GitHub 側に委ねる条件なので、独自判定のための再観測契機にはしない |

2回の取得が一致することは、取得間の整合性を示します。将来の状態の保証やレビュー完了の証拠にはしません。観測中・公開直前の対象コミットの一致確認は、Action 独自の判定にも引き続き必要です。

現行版の実行契機は[運用と移行](workflow.md)に記載しています。レビュー投稿・base branch の更新・時間経過では独自判定も自動更新されないため、最新の状態が必要なときは Run workflow で再観測します。固定時間の待機や定期実行でレビュー完了を推測する方式は採用しません。

## v0.6.0 からの変更

v0.6.0 にあった重複を、設定 version 4 のソースで次のように整理しました。

| v0.6.0 の設定・判定 | version 4 の扱い |
| --- | --- |
| `minimum_approvals`、`current_head_approvals` | 全 PR の最低承認数は Ruleset に任せ、Action の設定・判定から除く |
| `require_resolved_threads`、`review_threads` | 会話の解決は Ruleset に任せ、Action の設定・判定から除く |
| `github_review`、`change_requests` | GitHub のレビュー可否を再判定する条件から除く |
| `unchanged_ci_definitions` | パスだけによる対応要求を除き、CODEOWNERS と Ruleset の推奨設定へ移す |
| `open_pr`、`ready_for_review`、`mergeable` | GitHub のマージ可否を表す総合判定から除く。対象の選択に必要な状態は参照する |
| `stale_change_review` | 履歴から追加確認を要求する独自条件として残す |
| 観測の整合性・取得エラー | 独自判定に必要な情報を対象に確認する |

ラベルの説明とサマリーも追加確認の範囲へ合わせ、PR のマージ可否を含む総合判定は削除しました。設定の削除を伴うため、対応リリースと[移行手順](workflow.md#version-3-から-version-4-への移行)を揃えて切り替えます。version 3 の配布版へ設定だけを先行導入すると、検証で失敗します。
