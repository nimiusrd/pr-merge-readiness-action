起動メッセージの `PMR_VERSION` と `PMR_SOURCE_SHA` を使い、`docs/releases.md` の工程1から工程5を、この順と境界で実行する。コマンドと完了条件は `docs/releases.md` に従う。

`PMR_VERSION` は未使用の `vMAJOR.MINOR.PATCH`、`PMR_SOURCE_SHA` は工程1で選んだ main CI の40桁 SHA である。どちらかが無い場合は終了する。`package.json` の version、既存タグ、ラベルからは決めない。

- `devcontainer exec --workspace-folder .` は付けず、同じ引数を Node.js 24 で直接実行する。
- ブランチ名は実行環境が要求する接頭辞に合わせる。要求が無ければ配布準備は `prepare-$PMR_VERSION`、参照更新は `refs-$PMR_VERSION` とする。シェル例の `codex/prepare-$PMR_VERSION` は、同じ手順を手元で進めるときの名前である。
- `evidence/` はコミットしない。実測 JSON・API 応答・ログも Git に含めない。
- 配布準備 PR と参照更新 PR は draft で開く。マージ、承認、Ready への変更、自動マージの有効化はしない。
- CI が失敗しても製品ソースは変更しない。ローカルの `npm run build` で `dist/` を差し替えない。
- 配布準備 PR を開いたあと、その PR が main へマージされるまで待つ。コメント、レビュー、PR 上の CI 成功では工程3へ進まない。
- CI の完了を待ち、未完了のまま次の工程へ進まない。結果を確認できない場合は、確認できた URL を残して終了する。
- 工程3の確認が全て一致したあとだけ、文書どおり注釈付きタグを push する。タグの削除、移動、force push はしない。push が権限で拒否された場合は、確認結果と push するコマンドを残して終了する。
- `gh release create` と Release workflow の手動実行はしない。公開はタグ push で起動する Release workflow に任せる。
- 工程4が一致してから工程5の参照更新 PR を開き、そこで終了する。運用 workflow が旧版の SHA を指しているときは、SHA だけを新コミットへ付け替えない。`docs/workflow.md` の移行手順を同じ PR で完了し、`npm run check:workflows` が成功することを確認する。
- 失敗したときは `docs/releases.md` の「失敗時の対応」に従う。既存タグは動かさない。

1. 工程1を実行する。起動メッセージの SHA が main の祖先であり、その SHA への main の push CI が工程1の条件を全て満たすことを確認する。満たさなければ終了する。
2. 工程2を実行する。artifact を配置して検証し、配布準備 PR を draft で開く。ステージした差分は `dist/` の3ファイルだけにする。
3. レビューされ、main へマージされるまで待つ。
4. 工程3を実行する。マージコミットを `PMR_RELEASE_SHA` とし、main の push CI とソース差分を確認する。製品ソース・依存・ビルド設定が変わっていればタグを作らず終了する。確認後にタグを push し、Release workflow の `verify` と `publish` の両方が成功するまで待つ。失敗した run の URL を残し、タグは削除も移動もしない。
5. 工程4を実行する。タグが指すコミット、Immutable Release、asset、タグ内の `dist/` が一致することを確認する。
6. 工程5を実行する。参照更新 PR を draft で開いて終了する。マージしない。
