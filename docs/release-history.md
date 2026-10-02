# リリース履歴

現在の公開方法は[リリース手順](releases.md)を参照してください。新しいリリース用の配布ブランチは作らず、配布準備 PR を main にマージし、そのコミットへのタグ push で公開します。

各リリースには、バージョン・変更要約・配布準備 PR・main CI run URL とソース SHA・マージ後の CI run URL・Release run URL と配布用 SHA・検証要約を記録します。実測 JSON・API 応答・ログは Git 管理せず、`evidence/` または Actions artifacts に保存します。

## 旧方式での公開

既存 v0.4.0 は旧方式で公開したため、タグと `codex/releases/v0.4.0` は配布用 SHA `107e80a91574e277ea3c13e41aeff7710cae77e2` を指したまま保持します。過去のタグ・コミットは書き換えず、v0.5.0〜v0.7.0 は旧 Release workflow が main に配布物をコミットしてタグを作成しました。

[v0.5.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/34994639892)では、ソース `e6d305d05cce80eae0411cfb33845b4aef8a6e58` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `4790eda7e56840c18a98a1d4c135ab03c119b5f2` を main とタグに公開しました。`labels = "auto"` に対応する最初のリリースです。

[v0.5.1 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35000713461)では、ソース `2fdf61530ceec44a788cd13b9a0c00ef22cbbe2a` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `38abf77191ca0801d10dd6bd8c9d387bdad4b911` を main とタグに公開しました。Immutable Release として公開済みで、TOML の `action_ref` と Action SHA の一致制約を削除しています。

[v0.6.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35113264395)では、ソース `001c3be5f3c8e43009033331eaac03f6f15c5683` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `568c7441e16afa46db11bc84f1e4708e6525a404` を公開しました。設定 version 3 と単一実行への簡素化、更新時刻だけの変化による観測失敗の修正を含みます。

[v0.7.0 の Release run](https://github.com/nimiusrd/pr-merge-readiness-action/actions/runs/35344205607)では、ソース `55b6a9488b8928bd89d0279198b14c2322ca9dcf` のテスト・静的検査・両 CPU のバイナリ検証が成功し、配布用コミット `fc422aad51c2a719cc7b625afb5e3939b4f52868` を公開しました。設定 version 4 に対応し、GitHub Ruleset と重複する判定を削除しています。設定と参照 SHA を同時に移行してください。
