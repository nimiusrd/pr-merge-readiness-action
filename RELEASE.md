# リリースの実行

名前 `Release` の Cursor Automation は、このファイルに従って [リリース手順](docs/releases.md) の工程1から工程5を実行します。コマンドと完了条件の正本はリリース手順です。実行順、起動方法、途中で止める条件はこのファイルです。

Automation が配布準備 PR を開き、人がレビューして main にマージします。マージ後の確認が終わってから Automation がそのコミットへタグを push し、Release workflow が公開します。続けて Automation が参照更新 PR を開きます。

## プロンプト

Automation のプロンプトは次の1文にします。手順の変更は、このファイルと [リリース手順](docs/releases.md) を更新します。

> 起動メッセージの `PMR_VERSION` と `PMR_SOURCE_SHA` を使い、`RELEASE.md` に従ってリリース手順を実行する。

## 登録

[Automations](https://cursor.com/automations/new) に次の内容で保存します。保存先は Cursor の Automation 設定です。

| 項目       | 設定                                                                                                               |
| ---------- | ------------------------------------------------------------------------------------------------------------------ |
| 名前       | `Release`                                                                                                          |
| リポジトリ | `nimiusrd/pr-merge-readiness-action` の `main`                                                                    |
| トリガー   | Webhook のみ                                                                                                       |
| ツール     | Pull request の作成を有効にする。Pull request へのコメント投稿を有効にし、承認は無効にする。Memories は無効にする |
| 実行者     | 公開担当者本人。ブランチとタグの push は担当者の権限で行う                                                         |
| プロンプト | 上記の1文                                                                                                          |

スケジュールと Pull request merged は付けません。保存すると Webhook URL と API キーが発行されます。URL とキーは Git に含めず、公開のたびに `context` へ二つの値を入れて POST します。`context` は起動メッセージに追加されます。`PMR_VERSION` は未使用の `vMAJOR.MINOR.PATCH`、`PMR_SOURCE_SHA` は工程1で選んだ main CI の40桁 SHA です。

```sh
curl -sS -X POST "$PMR_RELEASE_WEBHOOK_URL" \
  -H "Authorization: Bearer $PMR_RELEASE_WEBHOOK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"context":"PMR_VERSION=v0.8.0\nPMR_SOURCE_SHA=CIで検証した40桁SHAに置き換える"}'
```

起動メッセージに `PMR_VERSION` または `PMR_SOURCE_SHA` が無い場合は終了します。`package.json` の version、既存タグ、ラベルからは決めません。

## 実行時の境界

Automation は、[リリース手順](docs/releases.md) のコマンドを同じ順で実行します。併せて次を守ります。

- `devcontainer exec --workspace-folder .` は付けず、同じ引数を Node.js 24 で直接実行する。
- ブランチ名は実行環境が要求する接頭辞に合わせる。要求が無ければ配布準備は `prepare-$PMR_VERSION`、参照更新は `refs-$PMR_VERSION` とする。シェル例の `codex/prepare-$PMR_VERSION` は、同じ手順を手元で進めるときの名前である。
- `evidence/` はコミットしない。実測 JSON・API 応答・ログも Git に含めない。
- 配布準備 PR と参照更新 PR は draft で開く。実行環境の pull request 作成機能を使い、使えない場合だけ `gh pr create` を使う。マージ、承認、Ready への変更、自動マージの有効化はしない。
- 配布準備 PR を開いた直後、その PR に `@cursor autofix off` とコメントする。CI の失敗を製品ソースの変更やローカル `npm run build` で直さない。
- 配布準備 PR を開いたあと、その PR が main へマージされるまで、その PR の完了通知を購読して待つ。コメント、レビュー、PR 上の CI 成功では工程3へ進まない。
- CI の完了も、対象コミットの完了通知を購読して待つ。未完了のまま次の工程へ進まない。購読できず結果も確認できない場合は、確認できた URL を残して終了する。
- 工程3の確認が全て一致したあとだけ、文書どおり注釈付きタグを push する。タグの削除、移動、force push はしない。push が権限で拒否された場合は、確認結果と push するコマンドを残して終了する。
- `gh release create` と Release workflow の手動実行はしない。公開はタグ push で起動する Release workflow に任せる。
- 工程4が一致してから工程5の参照更新 PR を開き、そこで終了する。運用 workflow が旧版の SHA を指しているときは、SHA だけを新コミットへ付け替えない。[運用と移行](docs/workflow.md)の移行手順を同じ PR で完了し、`npm run check:workflows` が成功することを確認する。
- 失敗したときは [リリース手順](docs/releases.md) の「失敗時の対応」に従う。既存タグは動かさない。

## 起動から完了まで

1. [リリース手順](docs/releases.md)の工程1を実行する。起動メッセージの SHA が main の祖先であり、その SHA への main の push CI が工程1の条件を全て満たすことを確認する。満たさなければ終了する。
2. 工程2を実行する。artifact を配置して検証し、配布準備 PR を draft で開く。ステージした差分は `dist/` の3ファイルだけにする。
3. 人がレビューして main へマージするまで待つ。
4. 工程3を実行する。マージコミットを `PMR_RELEASE_SHA` とし、main の push CI とソース差分を確認する。製品ソース・依存・ビルド設定が変わっていればタグを作らず終了する。確認後にタグを push し、Release workflow の `verify` と `publish` の両方が成功するまで待つ。失敗した run の URL を残し、タグは削除も移動もしない。
5. 工程4を実行する。タグが指すコミット、Immutable Release、asset、タグ内の `dist/` が一致することを確認する。
6. 工程5を実行する。参照更新 PR を draft で開いて終了する。マージしない。
