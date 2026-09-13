#!/usr/bin/env bash
# 利用側の project、仮想環境、Python モジュールを読み込まない。
set -euo pipefail
PMR_PYTHON="$(env -u UV_CONFIG_FILE -u UV_PROJECT -u UV_PYTHON \
  uv python find --system --managed-python --no-project --no-config 3.14)"
exec "$PMR_PYTHON" -I -B "$(dirname -- "$0")/cli.py" "$@"
