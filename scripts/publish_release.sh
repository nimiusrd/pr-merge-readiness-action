#!/usr/bin/env bash
# 検証済みのタグ内のバイナリを公開する。main とタグは変更しない。
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

Ubuntu 26.04 以降の Linux x64 用の Python 3.14 同梱バイナリです。arm64・Windows・macOS は対象外です。
利用側の uses を配布用コミット $release_sha に固定してください。
設定 version 4 が必要です。docs/workflow.md に従って設定と workflow を同時に移行してください。
実行時に uv・Python の導入やビルドは行いません。
EOF
tar -C dist/linux-x64 -czf "$artifacts_dir/pr-merge-readiness-linux-x64.tar.gz" \
  pr-merge-readiness SHA256SUMS
gh release create "$RELEASE_VERSION" --repo "$GITHUB_REPOSITORY" --verify-tag \
  --title "$RELEASE_VERSION" --notes-file "$notes" \
  "$artifacts_dir/pr-merge-readiness-linux-x64.tar.gz"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  cat "$notes" >> "$GITHUB_STEP_SUMMARY"
fi
