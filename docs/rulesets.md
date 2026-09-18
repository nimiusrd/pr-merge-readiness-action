# 推奨 Ruleset

この Action と併用する GitHub 側の設定指針です。[Action の責務](responsibilities.md)に従い、GitHub が提供するマージ条件は GitHub 側で管理します。

## 対象と推奨設定

通常は default branch を対象とする branch ruleset を作成し、有効な状態（Active）で運用します。承認者の人数、CI の Check 名、CODEOWNERS の担当者は利用先で選びます。

| 設定 | 推奨する使い方 |
| --- | --- |
| PR を経由した変更 | 対象ブランチへの通常の変更に PR を要求する |
| 必要な承認数 | レビュー体制に合わせて設定する。独立したレビュアーを置くチームでは1件以上を基本とし、単独運用では承認を必須にできる体制かを判断する |
| 変更後の再承認 | 差分が変わった際の承認の無効化を有効にする。運用上必要なら、最後の push を行った人以外の承認を要求する方式を選ぶ |
| 会話の解決 | マージ前にレビュー会話の解決を要求する |
| 必須の CI | 利用先のテスト・静的検査などを指定する。base の最新状態との整合性が必要なら、マージ前のブランチ最新化も要求する |
| コード所有者の承認 | CI 定義など担当者レビューが必要なパスを CODEOWNERS で指定し、所有者の承認を要求する |
| 履歴の保護 | 対象ブランチの削除と force push を制限する |

承認数・変更要求・承認の鮮度・会話解決・CI の成立は GitHub のマージ条件として確認します。具体的なルールと利用可能なプランは [GitHub の Ruleset 公式資料](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)を参照してください。

この Action の `readiness` job は、追加確認が必要な場合でも処理自体は成功します。必須 CI として登録しても追加確認の完了を強制できないため、推奨する必須 Check に含めません。

## インポート用 JSON

[recommended-ruleset.json](../examples/recommended-ruleset.json) を保存して利用できます。以下も同じ内容です。実リポジトリの設定をエクスポートしたものではなく、個別の ID を含まない配布用のひな形です。

初期値は承認1件・CODEOWNERS の承認・会話解決・必須 CI・削除と force push の制限です。CI 名を調整する前に適用しないよう `enforcement` は `disabled` にしています。取り込みと調整の後に `active` へ変更してください。

```json
{
  "name": "推奨マージ条件",
  "target": "branch",
  "enforcement": "disabled",
  "bypass_actors": [],
  "conditions": {
    "ref_name": {
      "include": [
        "~DEFAULT_BRANCH"
      ],
      "exclude": []
    }
  },
  "rules": [
    {
      "type": "deletion"
    },
    {
      "type": "non_fast_forward"
    },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          {
            "context": "REPLACE_WITH_YOUR_CI_CHECK_NAME"
          }
        ]
      }
    }
  ]
}
```

取り込み時に次を調整します。

| 項目 | 調整内容 |
| --- | --- |
| `required_status_checks` 内の `context` | `REPLACE_WITH_YOUR_CI_CHECK_NAME` を、利用先で実行される Check 名に置き換える。複数ある場合は要素を追加する。`readiness` は含めない |
| `required_approving_review_count` | 独立したレビュアーがいる運用では1件以上。通常の承認を必須にしない運用では0にする |
| `require_code_owner_review` | CODEOWNERS を用意する。所有者承認を必須にしない運用では `false` にする |
| `enforcement` | 調整済みのルールを適用するときに `active` にする |
| `conditions.ref_name` | default branch 以外も保護する場合に対象を調整する |

CI の `integration_id` は特定の環境に固定しないため省略しています。必要なら取り込み先で CI の発行元アプリも指定します。bypass は空です。本リポジトリのように配布処理が main へ直接 push する場合は、後述のリリース方式との整合性も確認します。

GitHub のリポジトリ設定から Rulesets を開き、New ruleset のメニューで Import a ruleset を選んで JSON を読み込み、内容を調整して作成します。[公式のインポート手順](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/managing-rulesets-for-a-repository#importing-a-ruleset)と[各フィールドの定義](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset)を参照してください。

## CI 定義のレビュー

CI 定義の変更に担当者の承認を求める場合は、CODEOWNERS で `/.github/workflows/` と `/.github/CODEOWNERS` を所有者に割り当て、Ruleset 側で所有者レビューを要求します。必要に応じて、workflow から呼び出すスクリプトや設定ファイルも対象にします。

CODEOWNERS は base branch に配置し、利用先で書き込み権限を持つ実在のユーザー・チームを指定します。ファイルを置くだけで承認が必須になるわけではありません。詳細は [GitHub の CODEOWNERS 公式資料](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)を参照してください。

これは CI 定義に承認を求めるための設定であり、任意の変更を一律に禁止する設定ではありません。Action で `.github/workflows/` の変更を再検出して対応事項にする必要はありません。

## 適用と運用

ルールが未設定でも、この Action が代わりに強制する設計にはしません。利用先の管理者が Ruleset を設定し、変更要求や未解決会話などがある場合の GitHub のマージ表示を確認します。Action のラベルは補足情報として扱います。

bypass を設ける場合は対象者・用途を限定し、通常の変更が上記のルールを通るように運用します。この Action は Ruleset の作成・変更・適用状況の監査を行いません。この文書も利用先や本リポジトリの設定を変更するものではありません。

本リポジトリの現行 Release workflow は、検証済みバイナリを main に直接コミットして配布します。上記の PR 必須化を本リポジトリへ適用する際は、[リリース手順](releases.md)との整合性を別途設計します。ここに記載した推奨設定が、現行 Release workflow とそのまま両立することを意味しません。

GitHub の仕様を確認した日付: 2026-09-18。
