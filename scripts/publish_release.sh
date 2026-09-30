#!/usr/bin/env bash
# 検証済みのタグ内の JavaScript バンドルを公開する。main とタグは変更しない。
set -euo pipefail
bash "$(dirname "${BASH_SOURCE[0]}")/validate_release.sh"
release_sha="$(git rev-parse HEAD)"
artifacts_dir="$(mktemp -d)"
trap 'rm -rf "$artifacts_dir"' EXIT

notes="$artifacts_dir/notes.md"
cat > "$notes" <<EOF
リリース: $RELEASE_VERSION
配布用コミット: $release_sha
配布ブランチ: main
検証 run: ${GITHUB_SERVER_URL:-https://github.com}/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID

Node.js 24 で実行する TypeScript 製 Action の JavaScript バンドルです。
利用側の uses を配布用コミット $release_sha に固定してください。
設定 version 4 が必要です。docs/workflow.md に従って設定と workflow を同時に移行してください。
利用側で Node.js のセットアップ・依存インストール・ビルドは行いません。
EOF
tar -C dist -czf "$artifacts_dir/pr-merge-readiness-action.tar.gz" \
  index.js cli.js package.json THIRD_PARTY_LICENSES.txt SHA256SUMS
gh release create "$RELEASE_VERSION" --repo "$GITHUB_REPOSITORY" --verify-tag \
  --title "$RELEASE_VERSION" --notes-file "$notes" \
  "$artifacts_dir/pr-merge-readiness-action.tar.gz"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  cat "$notes" >> "$GITHUB_STEP_SUMMARY"
fi
