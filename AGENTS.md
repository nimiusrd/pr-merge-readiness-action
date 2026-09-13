# 開発ルール

- 日本語で記述・回答する。
- コマンドは `devcontainer exec --workspace-folder . <command>` で Python 3.11 の Dev Container 内で実行する。Git 操作はホストで行う。
- 提出前に `python -B -m unittest discover -s tests -t .`、`ruff check .`、`ruff format --check .`、`python scripts/check_workflows.py` を実行する。
- 旧システムの CLI・policy・artifact との互換処理は追加しない。
- 実測 JSON・API 応答・証跡を Git 管理しない。`evidence/` または Actions artifacts を使い、文書には run URL と検証要約を記載する。
- 自動マージ・自動承認・定期実行・必須 Check 登録を追加しない。
