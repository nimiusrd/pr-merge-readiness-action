# 1回の Action 呼出しで処理する

[完全な workflow 例](../examples/pr-merge-readiness.yml)と[最小設定](../examples/minimal.toml)を利用側の `.github` にコピーします。設定の `action_ref` と workflow の `uses:` を同じ40桁 SHA に固定し、レビュー条件を合わせてください。現在記載している公開済み SHA `27ca8908b993e93eb319c70e7231fa7fd1999b05` は、新しい `pull_request` の自動観測と fork・Dependabot 除外に未対応です。新実装の公開後に、起動条件と両方の SHA をまとめて更新してください。

通常の workflow から Composite Action を1回呼び出します。呼び出し側は起動条件、手動入力、runner、timeout、権限、concurrency を管理し、処理の分岐・順序・レポート保存を Action に任せます。checkout、再利用可能 workflow、生成コマンドは不要です。

## イベント

| イベント | Action 内の動作 |
| --- | --- |
| 設定・workflow の `push` | push 対象 SHA の TOML を検証して終了 |
| 通常 PR の opened／reopened／synchronize／ready_for_review／converted_to_draft、base の編集（`pull_request`） | PR head の TOML を検証 → default branch の設定で当該 PR を観測 → 保存 → Check |
| 通常 PR の終了（`pull_request: closed`） | PR head の TOML を検証 → default branch の設定で当該 PR を観測 → 保存 → Check |
| fork・Dependabot・作成元リポジトリが削除された PR の `pull_request` | 設定取得・観測・公開を省略 |
| Run workflow、PR 番号指定 | 指定 PR を観測 → 保存 → Check |
| Run workflow、番号なし | 全 open PR を観測 → 保存 → Check |
| Run workflow、`update-labels = true` | 全 open PR を観測 → 保存 → Check → ラベル |

通常 PR は、作成元とマージ先が同じリポジトリで、作成者が `dependabot[bot]` 以外の PR を指します。PR 状態変更には `pull_request` を使い、タイトル・本文だけの編集は処理を省略します。承認イベントと日次実行は起動条件にしません。手動ラベル更新と PR 番号指定は併用できません。Check が無効ならその公開段階を省略します。CI の開始・完了・再実行では起動しません。競合中の PR では `pull_request` が起動しないため、Run workflow で再評価します。レビュー・スレッド解決・base ブランチへの新しい push をすぐに反映する場合も Run workflow を使います。[GitHub の起動条件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)を参照してください。

参考 Check も通常 PR だけを対象にし、公開直前の API 応答でリポジトリ ID と作成者を確認します。手動実行で fork・Dependabot の PR を観測した場合はレポートを保存し、Check 公開は省略します。既存の参考 Check は更新しません。

open PR の競合判定が `UNKNOWN` の場合、観測開始時と最終確認時のそれぞれで、2秒間隔・最大5回の追加取得を行います。確定した場合は同時に取得した head・base などを含む情報で観測と鮮度の確認を行います。上限後も未確定なら `UNKNOWN` を保存し、充足扱いにはしません。その後の確定を反映するには新しい PR イベントまたは Run workflow が必要です。closed／merged PR は確定待ちをしません。

ラベルの同期タイミングは、`update-labels = true` を指定した手動実行だけです。レビュー・変更履歴の条件で、作成元とマージ先が同じリポジトリの open PR にラベルを付けます。公開直前にリポジトリ ID と PR 作成者を確認し、fork PR、作成元リポジトリが削除された PR、作成者が `dependabot[bot]` の PR は付与対象から外して管理ラベルを除去します。実行者が人間でも Dependabot の PR は対象外です。closed／merged PR の管理ラベルも除去します。Draft・競合・open/closed 状態だけではラベル用の判定を変えません。PR 状態のイベントは、参考 Check とレポートを更新するために維持します。

## 設定と権限

運用時は default branch の設定 SHA を一度確定し、後続処理で同じ設定を使います。途中で default branch が進んでも再解決しません。設定・レポートの出所と Action の実ソースの SHA を照合します。通常 PR では最初に PR head の TOML を検証し、不正なら観測・公開へ進みません。有効な提案でも、観測・公開の policy は default branch から別に取得します。PR のソースコードは実行しません。

`pull_request` の観測開始時・終了時に、API の head SHA と設定を検証したイベントの head SHA が一致することを要求します。不一致なら観測全体を失敗として記録し、PR の判定レポートと Check を公開しません。観測後に追加 push された場合も、Check 公開時の head SHA がイベントと異なれば公開を省略します。古いイベントの検証結果を新しい head に流用せず、新しいイベントで再評価します。

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

ローカルで TOML を検証する場合は、固定版 Action の CLI を使用できます。

```sh
uv python install --no-config 3.14
bash /absolute/path/to/pr-merge-readiness-action/run.sh validate-config \
  --config .github/pr-merge-readiness.toml
```

Action 更新時は TOML の `action_ref` と workflow の `uses:` を同じ公開済み SHA にまとめて変更します。明示した `action-ref` 入力がある場合はそれも更新します。

同じ Check・ラベルを更新する既存 writer から切り替える場合は、実行終了を確認してから入口を一つの変更で切り替えます。切り戻しは workflow と TOML を同時に revert します。旧 artifact の変換は行いません。

## 公開後の移行

### pull_request への移行

このリポジトリの運用中の `.github/` は、公開済み実装 `27ca8908b993e93eb319c70e7231fa7fd1999b05` と従来の起動条件を使用しています。新実装を公開してから、以下を同じ変更で反映します。利用側も同じ手順です。

1. `uses:` と TOML の `action_ref` を、新実装を含む同じ公開済み40桁 SHA に更新します。
2. `pull_request_target` を削除し、`pull_request` に opened／reopened／synchronize／edited／ready_for_review／converted_to_draft／closed を設定します。既存の `pull_request.paths` は外して通常 PR 全体を対象にします。`push.paths` と手動入力は維持します。
3. 通常 PR の更新で参考 Check が更新され、fork・Dependabot では省略されることを確認します。競合中の通常 PR は手動実行で確認します。
4. Run workflow の `update-labels = true` で同期し、fork・Dependabot に残る管理ラベルを除去します。

旧 SHA のまま起動条件だけ変更すると、`pull_request` は設定検証だけで終了し、自動 Check 更新が止まります。設定・レポートの schema version は今回変更しません。

### v0.4.0 からの移行

v0.4.0 から切り替える利用側は、上記と合わせて以下を反映してください。version 2 の設定を旧 Action SHA と組み合わせると設定検証に失敗します。

1. version 2 対応の公開済み40桁 SHA を確定し、`uses:` と TOML の `action_ref` に同じ値を設定します。
2. TOML を version 2 にし、`[ci]` と `[[ci.required_checks]]` を削除します。
3. workflow の `workflow_run` トリガーと `actions: read`・`statuses: read` を削除します。通常 PR の状態変更は `pull_request` で直接観測されます。`mark` operation は廃止しています。
4. workflow と設定を同時に反映し、Run workflow の `update-labels = true` で全 open PR を同期します。CI を含む旧ラベルの付与は open／closed PR から除去されます。他のラベルと旧ラベルのリポジトリ内の定義は削除しません。
5. PR 更新で当該 PR が観測されること、CI 開始・完了では起動しないこと、JSON・Summary・参考 Check・ラベルに CI の実行状態が集約されないことを確認します。

観測・レポートは schema version 2 です。CI 状態と `mergeStateStatus` の項目を削除し、PR・レビューの鮮度を検証します。ラベル用の `label_assessment` と `review_stable` は PR の表示状態から独立させ、head・base・レビュー内容などの変化は引き続き検出します。参考 Check は引き続き常に neutral で、CI 成功やマージ許可を表しません。
