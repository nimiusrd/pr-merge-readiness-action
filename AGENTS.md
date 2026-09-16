# 開発ルール

- 日本語で記述・回答する。
- コマンドは `devcontainer exec --workspace-folder . <command>` で Python 3.14 の Dev Container 内で実行する。Python と開発依存は uv で管理し、`.python-version`・`pyproject.toml`・`uv.lock` を正本にする。Git 操作はホスト、GitHub CLI はサンドボックス外で実行してよい。
- 提出前に `uv sync --locked`、`uv run --locked pytest`、`uv run --locked ruff check .`、`uv run --locked ruff format --check .`、`uv run --locked mypy`、`uv run --locked python scripts/check_workflows.py` を実行する。
- Composite Action は `run-binary.sh` から同梱バイナリを起動する。パッケージング・launcher・CLI を変更した場合は、`uv sync --locked --group build`、`uv run --locked --group build python scripts/build_binary.py` と `PMR_TEST_BINARY=dist/<platform>/pr-merge-readiness uv run --locked --group build pytest tests/test_binary.py` でも検証する。コマンドは Dev Container 内で実行する。
- `dist/` は Release workflow が検証済みバイナリを main にコミットし、そのコミットにリリースタグを付ける。実装 PR にはローカルで再ビルドした生成物の差分を含めない。利用側はリリースタグが指す40桁 SHA に固定する。公開手順は `docs/releases.md` を参照する。
- テストは pytest の関数・assert・fixture・parametrize で記述する。unittest.TestCase は使わない。
- 旧システムの CLI・policy・artifact との互換処理は追加しない。
- 通常の workflow の step から Composite Action を直接利用する。再利用可能 workflow（workflow_call / jobs.<id>.uses）と workflow 生成器は提供しない。
- 実測 JSON・API 応答・証跡を Git 管理しない。`evidence/` または Actions artifacts を使い、文書には run URL と検証要約を記載する。
- 自動マージ・自動承認・定期実行・必須 Check 登録を追加しない。
