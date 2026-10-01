#!/usr/bin/env bash
# 検証済みのタグ内の JavaScript バンドルを公開する。main とタグは変更しない。
set -euo pipefail
bash "$(dirname "${BASH_SOURCE[0]}")/validate_release.sh"
release_sha="$(git rev-parse HEAD)"
artifacts_dir="$(mktemp -d)"
trap 'rm -rf "$artifacts_dir"' EXIT

notes="$artifacts_dir/notes.md"
run_url="${GITHUB_SERVER_URL:-https://github.com}/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
# 素の SHA は Job Summary 上で短縮される。40桁はコード表記で残す。ブランチ名は書かない。
cat > "$notes" <<EOF
リリース: $RELEASE_VERSION
配布用コミット: \`$release_sha\`
検証 run: $run_url

固定参照: \`$GITHUB_REPOSITORY@$release_sha\`

Node.js 24 で実行する TypeScript 製 Action の JavaScript バンドルです。
利用側の uses を固定参照に合わせてください。
閾値は with.stale-change-review-days で指定します。docs/workflow.md に従って workflow を移行してください。
利用側で Node.js のセットアップ・依存インストール・ビルドは行いません。
EOF
tar -C dist -czf "$artifacts_dir/pr-merge-readiness-action.tar.gz" \
  index.js package.json SHA256SUMS
gh release create "$RELEASE_VERSION" --repo "$GITHUB_REPOSITORY" --verify-tag \
  --title "$RELEASE_VERSION" --notes-file "$notes" \
  "$artifacts_dir/pr-merge-readiness-action.tar.gz"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  cat "$notes" >> "$GITHUB_STEP_SUMMARY"
fi
