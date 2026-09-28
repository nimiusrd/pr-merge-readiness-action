#!/usr/bin/env bash
# リリースコミットに同梱した実行ファイルだけを起動する。
set -euo pipefail
case "$(uname -s)/$(uname -m)" in
  Linux/x86_64) ;;
  *) echo 'PR Merge Readiness requires a Linux x64 runner.' >&2; exit 1 ;;
esac
binary="$(cd -- "$(dirname -- "$0")" && pwd)/dist/linux-x64/pr-merge-readiness"
if [[ ! -x "$binary" ]]; then
  echo 'Release binary missing. Pin a release commit containing dist/, or build it with uv run --locked --group build python scripts/build_binary.py.' >&2
  exit 1
fi
exec "$binary" "$@"
