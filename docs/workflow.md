# 1回の Action 呼出しで処理する

[完全な workflow 例](../examples/pr-merge-readiness.yml)と[最小設定](../examples/minimal.toml)を利用側の `.github` にコピーし、レビュー条件を合わせてください。workflow の `uses:` はリリースタグが指す40桁 SHA に固定します。現在の例は v0.5.1 の配布用 SHA `38abf77191ca0801d10dd6bd8c9d387bdad4b911` を使用しています。TOML の `action_ref` は不要です。

通常の workflow から Composite Action を1回呼び出します。呼び出し側は起動条件、手動入力、runner、timeout、権限、concurrency を管理し、処理の分岐・順序・レポート保存を Action に任せます。checkout、再利用可能 workflow、生成コマンドは不要です。

## イベント

| イベント | Action 内の動作 |
| --- | --- |
| 設定・workflow の `push` | push 対象 SHA の TOML を検証して終了 |
| 通常 PR の opened／reopened／synchronize／ready_for_review／converted_to_draft、base の編集（`pull_request`） | PR head の TOML を検証 → default branch の設定で当該 PR を観測 → 保存 → Check → `labels = "auto"` なら当該 PR のラベルを更新 |
| 通常 PR の終了（`pull_request: closed`） | PR head の TOML を検証 → default branch の設定で当該 PR を観測 → 保存 → Check → `labels = "auto"` なら当該 PR の管理ラベルを除去 |
| fork・Dependabot・作成元リポジトリが削除された PR の `pull_request` | 設定取得・観測・公開を省略 |
| Run workflow、PR 番号指定 | 指定 PR を観測 → 保存 → Check |
| Run workflow、番号なし | 全 open PR を観測 → 保存 → Check |
| Run workflow、`update-labels = true` | 全 open PR を観測 → 保存 → Check → ラベル |

`labels = "auto"` は v0.5.0 以降の機能です。このリポジトリ自身は自動更新を有効にしています。利用例のラベル設定は既定の手動更新です。[自動ラベルへの移行](#自動ラベルへの移行)に従い、対応リリースの SHA と設定を同時に更新してください。

通常 PR は、作成元とマージ先が同じリポジトリで、作成者が `dependabot[bot]` 以外の PR を指します。PR 状態変更には `pull_request` を使い、タイトル・本文だけの編集は処理を省略します。承認イベントと日次実行は起動条件にしません。手動ラベル更新と PR 番号指定は併用できません。Check が無効ならその公開段階を省略します。CI の開始・完了・再実行では起動しません。競合中の PR では `pull_request` が起動しないため、Run workflow で再評価します。レビュー・スレッド解決・base ブランチへの新しい push をすぐに反映する場合も Run workflow を使います。[GitHub の起動条件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)を参照してください。

参考 Check も通常 PR だけを対象にし、公開直前の API 応答でリポジトリ ID と作成者を確認します。手動実行で fork・Dependabot の PR を観測した場合はレポートを保存し、Check 公開は省略します。既存の参考 Check は更新しません。

open PR の競合判定が `UNKNOWN` の場合、観測開始時と最終確認時のそれぞれで、2秒間隔・最大5回の追加取得を行います。確定した場合は同時に取得した head・base などを含む情報で観測と鮮度の確認を行います。上限後も未確定なら `UNKNOWN` を保存し、充足扱いにはしません。その後の確定を反映するには新しい PR イベントまたは Run workflow が必要です。closed／merged PR は確定待ちをしません。

ラベルは `labels = "auto"` の PR イベントで当該 PR を更新し、`update-labels = true` を指定した手動実行では全 open PR を同期します。既定の `labels = "manual"` は手動同期だけ、`"off"` はラベル更新を無効にします。`"auto"` でも手動実行時は明示的な `update-labels` 入力が必要です。レビュー・変更履歴の条件で、作成元とマージ先が同じリポジトリの open PR にラベルを付けます。公開直前にリポジトリ ID と PR 作成者を確認し、fork PR、作成元リポジトリが削除された PR、作成者が `dependabot[bot]` の PR は付与対象から外して管理ラベルを除去します。実行者が人間でも Dependabot の PR は対象外です。closed／merged PR の管理ラベルも除去します。自動実行では当該 PR だけを処理し、他の closed／merged PR の清掃は手動同期で行います。Draft・競合・open/closed 状態だけではラベル用の判定を変えません。

## 設定と権限

運用時は default branch の設定 SHA を一度確定し、後続処理で同じ設定を使います。途中で default branch が進んでも再解決しません。レポートの出所には実行中の Action SHA と設定コミット SHA を別々に記録し、公開時に両方の一致を確認します。v0.5.1 から、TOML の `action_ref` は参照せず、設定の互換性は `version` と各項目で検証します。通常 PR では最初に PR head の TOML を検証し、不正なら観測・公開へ進みません。有効な提案でも、観測・公開の policy は default branch から別に取得します。PR のソースコードは実行しません。

`pull_request` の観測開始時・終了時に、API の head SHA と設定を検証したイベントの head SHA が一致することを要求します。不一致なら観測全体を失敗として記録し、PR の判定レポート・Check・ラベルを公開しません。観測後に追加 push された場合も、Check・ラベル公開時の head SHA がイベントと異なれば公開を省略します。古いイベントの検証結果を新しい head に流用せず、新しいイベントで再評価します。

1 job は contents の read と、checks・pull-requests・issues の write を持ちます。`push` と個別の `validate-config` は読み取りだけで動作します。fork・Dependabot の PR イベントでは設定取得前に処理を省略します。

workflow を編集できる書き込み権限者は信頼対象です。この権限者は `permissions` 自体も編集できるため、Action の読み取り専用経路や同じ workflow 内の job 分離は workflow 定義の改変を防ぐ境界ではありません。外部 fork の `pull_request` は GitHub の読み取り専用 token 制限に従います。

Check writer の concurrency group は `autonomous-merge-check-writer`、`cancel-in-progress` は `false`、`queue` は `max` に統一します。設定検証から公開まで job 全体を直列化し、手動ラベル要求も同じキューで最大100件まで待機させます。後続の通常イベントで既存の手動要求を置き換えず、ラベル用の別 group は不要です。待機枠が満杯の場合は追加の要求がキャンセルされるため、その手動要求は空きができてから再実行してください。[GitHub のキュー仕様](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)を参照できます。

## 保存と失敗

Action が今回の観測を `pr-merge-readiness-RUN_ID-ATTEMPT` に30日保存します。公開はアップロード成功後に同じ job のレポートを読み、manifest・全レポートの出所と鮮度を確認してから実行します。別 run・attempt の artifact を探す fallback はありません。

- 準備失敗：`preparation-report/` の診断を専用 artifact に保存し、後続処理を止めます。
- 観測失敗：今回のレポートを保存します。有効な manifest がある場合は公開を試みますが、Action 全体の失敗は保持します。収集全体の失敗・不正な manifest は公開を拒否します。
- artifact 保存失敗：Check・ラベルを公開しません。
- Check 公開失敗：ラベルを更新しません。
- キャンセル：後続の公開を開始しません。

同じ run の再実行では、前 attempt の artifact が取得できなくなる場合があります。再観測には新しい Run workflow を使い、必要な証跡は再実行前に Git 外へ保存してください。

## 個別 operation と更新

`operation` を明示すると、`prepare`・`validate-config`・`observe`・`publish-checks`・`publish-labels` を個別に利用できます。read-only の観測だけを行う用途などで使います。個別利用では呼び出し側が同じ設定 SHA の引き継ぎ、artifact 保存、公開順序、限定権限を管理します。自動保存は既定の `run` だけが行います。

検証済み head への固定は、自動 `run` 内の観測・Check 公開に適用します。個別の `publish-checks` は各レポートの PR を対象とし、`pull_request` から呼んでもイベント元 PR の head に一律固定しません。

`publish-labels` は個別利用でも、PR イベントでは `labels = "auto"` と当該 PR だけの `single_pr` 観測を要求します。別 PR・追加 PR・空のレポート一覧を拒否し、取得済みの PR 情報がある場合は観測 head とイベント head も照合します。公開直前の API の head が異なる場合は更新を省略します。手動実行では `"auto"`／`"manual"`、`update-labels = true`、PR 番号なし、`all_open` 観測を要求します。設定・manifest・全レポートの検証を終えてからラベル定義や PR を更新します。

ローカルで TOML を検証する場合は、バイナリ版リリースを checkout して CLI を使用できます。Linux x64 / arm64 で動作します。

```sh
bash /absolute/path/to/release-checkout/run-binary.sh validate-config \
  --config .github/pr-merge-readiness.toml
```

Action 更新時は workflow の `uses:` を新しい公開済み SHA に変更します。明示した Action 入力の `action-ref` がある場合はそれも更新するか、省略して実際の参照から取得させます。v0.5.1 から TOML の `action_ref` の更新は不要です。

バイナリ版では、[Release workflow が作る配布用コミット](releases.md)の SHA を使用してください。現在の利用例は v0.5.1 の配布用 SHA `38abf77191ca0801d10dd6bd8c9d387bdad4b911` に固定しています。ソース checkout で開発する場合は、uv で Python 3.14 を用意して `bash run.sh validate-config --config <path>` を使用できます。

公開済み v0.4.0 / v0.5.0 は TOML の `action_ref` と実行 SHA を照合するため、更新 PR では default branch の旧値によって `config/action SHA mismatch` になる場合があります。v0.5.1 ではこの照合を削除し、設定が有効なら PR head・default branch に旧値が残っていても観測・公開へ進みます。参考用の readiness job を必須 Check として登録しないでください。

同じ Check・ラベルを更新する既存 writer から切り替える場合は、実行終了を確認してから入口を一つの変更で切り替えます。切り戻しは workflow と TOML を同時に revert します。旧 artifact の変換は行いません。

## 公開後の移行

### 設定と Action の SHA の分離

v0.5.1 から対応しています。設定 version は 2 のままです。

1. workflow の `uses:` を v0.5.1 以降のリリースタグが指す配布用コミット SHA に更新します。実行中の Action のリポジトリとフル SHA は引き続き検証します。
2. TOML の `action_ref` は削除できます。default branch や既存 PR に残っていても値は読み捨てるため、同時更新は不要です。運用設定と提案設定の両方で省略を受け付けます。
3. 更新 PR で観測・参考 Check が成功し、運用設定が `labels = "auto"` なら当該 PR のラベルが更新されることを確認します。レポートには新しい Action SHA と観測時の設定コミット SHA が記録されます。

v0.4.0 / v0.5.0 の `uses:` を残したまま先に `action_ref` を削除すると、そのバイナリの設定検証に失敗します。まず対応リリースへ更新してください。旧 Action を使う既存 PR は workflow の更新を取り込む必要があります。切り戻す場合は旧 Action が必要とする `action_ref` も戻します。

### 自動ラベルへの移行

`labels = "auto"` は v0.5.0 以降で使用できます。このリポジトリ自身は `"auto"`、利用例は既定の `"manual"` を使用しています。v0.4.0 の同梱バイナリは `"auto"` を拒否するため、Action の更新が必要です。

1. 対応リリースのタグが指す配布用コミットの40桁 SHA を取得します。現在の v0.5.1 の SHA は `38abf77191ca0801d10dd6bd8c9d387bdad4b911` です。
2. 利用側の workflow の `uses:` をその SHA に固定し、`[publication]` の `labels` を `"auto"` に変更します。v0.5.0 では TOML の `action_ref` も同じ SHA に揃え、v0.5.1 以降では省略します。`pull_request` の起動条件・権限・concurrency は完全な workflow 例のまま使えます。手動実行だけの workflow には、完全な例の `pull_request` 起動条件も追加します。
3. default branch への反映後、通常 PR の作成・追加 push で当該 PR に判定ラベルが付き、終了時に管理ラベルが除去されることを確認します。移行 PR と既存 PR の SHA 不一致は「個別 operation と更新」の扱いに従います。
4. 既存 open PR をまとめて更新する場合は、default branch の Run workflow で PR 番号を空にし、`update-labels = true` を指定します。

自動更新を止める場合は `labels = "manual"`、手動更新も止める場合は `"off"` に変更します。設定だけの変更は既存ラベルを除去しません。

### バイナリ版への移行

1. Release workflow の公開完了後、リリースタグが指す**配布用コミット**の40桁 SHA を取得します。今後のリリースは検証済みバイナリを main に反映したコミットへタグを付けます。ビルド開始時のソース SHA ではなく、公開後のタグが指す SHA を使用します。
2. `uses:` を配布用 SHA に変更します。TOML の `action_ref` は、v0.4.0 / v0.5.0 では同じ SHA を指定し、v0.5.1 以降では省略します。イベント、権限、設定・レポートの schema は変わりません。
3. マージ後、default branch の Run workflow で観測・artifact 保存・参考 Check を確認します。uv・Python のセットアップ step がなく、同梱バイナリが動くことを確認します。移行 PR の SHA 不一致は「個別 operation と更新」の扱いに従います。

Python と uv を利用側に用意する必要はありません。Linux x64 / arm64 の対応 runner を指定してください。

### pull_request への移行

このリポジトリの `.github/` と利用例は、公開済み実装 `38abf77191ca0801d10dd6bd8c9d387bdad4b911` と `pull_request` を使用しています。従来の `pull_request_target` を使っている利用側は、以下を同じ変更で反映します。

1. `uses:` を対応リリースの公開済み40桁 SHA に更新します。TOML の `action_ref` は「設定と Action の SHA の分離」の対応版に応じて扱います。
2. `pull_request_target` を削除し、`pull_request` に opened／reopened／synchronize／edited／ready_for_review／converted_to_draft／closed を設定します。既存の `pull_request.paths` は外して通常 PR 全体を対象にします。`push.paths` と手動入力は維持します。
3. マージ後に通常 PR の更新で参考 Check が更新され、fork・Dependabot では省略されることを確認します。競合中の通常 PR は手動実行で確認します。移行 PR 自体の SHA 不一致については「個別 operation と更新」を参照してください。
4. Run workflow の `update-labels = true` で同期し、fork・Dependabot に残る管理ラベルを除去します。

旧 SHA のまま起動条件だけ変更すると、`pull_request` は設定検証だけで終了し、自動 Check 更新が止まります。設定・レポートの schema version は今回変更しません。

### 設定 version 1 からの移行

設定 version 1 の旧実装から切り替える利用側は、上記と合わせて以下を反映してください。version 2 の設定を旧 Action SHA と組み合わせると設定検証に失敗します。

1. version 2 対応の公開済み40桁 SHA を確定し、`uses:` に設定します。TOML の `action_ref` は「設定と Action の SHA の分離」の対応版に応じて扱います。
2. TOML を version 2 にし、`[ci]` と `[[ci.required_checks]]` を削除します。
3. workflow の `workflow_run` トリガーと `actions: read`・`statuses: read` を削除します。通常 PR の状態変更は `pull_request` で直接観測されます。`mark` operation は廃止しています。
4. workflow と設定を同時に反映し、Run workflow の `update-labels = true` で全 open PR を同期します。CI を含む旧ラベルの付与は open／closed PR から除去されます。他のラベルと旧ラベルのリポジトリ内の定義は削除しません。
5. PR 更新で当該 PR が観測されること、CI 開始・完了では起動しないこと、JSON・Summary・参考 Check・ラベルに CI の実行状態が集約されないことを確認します。

観測・レポートは schema version 2 です。CI 状態と `mergeStateStatus` の項目を削除し、PR・レビューの鮮度を検証します。ラベル用の `label_assessment` と `review_stable` は PR の表示状態から独立させ、head・base・レビュー内容などの変化は引き続き検出します。参考 Check は引き続き常に neutral で、CI 成功やマージ許可を表しません。
