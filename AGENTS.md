# 開発ルール

- 日本語で記述・回答する。
- コマンドは `devcontainer exec --workspace-folder . <command>` で Python 3.14 の Dev Container 内で実行する。Python と開発依存は uv で管理し、`.python-version`・`pyproject.toml`・`uv.lock` を正本にする。Git 操作はホスト、GitHub CLI はサンドボックス外で実行してよい。
- 提出前に `uv sync --locked`、`uv run --locked pytest`、`uv run --locked ruff check .`、`uv run --locked ruff format --check .`、`uv run --locked mypy`、`uv run --locked python scripts/check_workflows.py` を実行する。
- テストは pytest の関数・assert・fixture・parametrize で記述する。unittest.TestCase は使わない。
- 旧システムの CLI・policy・artifact との互換処理は追加しない。
- 実測 JSON・API 応答・証跡を Git 管理しない。`evidence/` または Actions artifacts を使い、文書には run URL と検証要約を記載する。
- 自動マージ・自動承認・定期実行・必須 Check 登録を追加しない。
