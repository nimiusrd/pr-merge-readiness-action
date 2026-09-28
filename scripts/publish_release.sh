#!/usr/bin/env bash
# 同じ run の検証済みバイナリを main に同梱し、そのコミットをタグで公開する。
set -euo pipefail
[[ "$GITHUB_REF" == refs/heads/main ]]
[[ "$RELEASE_VERSION" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
test "$(git rev-parse HEAD)" = "$GITHUB_SHA"
test -z "$(git status --porcelain)"
remote_tag="$(git ls-remote --tags origin "refs/tags/$RELEASE_VERSION")"
test -z "$remote_tag"
remote_main="$(git ls-remote --exit-code origin refs/heads/main)"
test "$remote_main" = "$GITHUB_SHA"$'\trefs/heads/main'

artifacts_dir="$(mktemp -d)"
trap 'rm -rf "$artifacts_dir"' EXIT

gh run download "$GITHUB_RUN_ID" --repo "$GITHUB_REPOSITORY" \
  --name binary-linux-x64 --dir "$artifacts_dir/linux-x64"
(cd "$artifacts_dir/linux-x64" && sha256sum --check SHA256SUMS)
install -D -m 755 "$artifacts_dir/linux-x64/pr-merge-readiness" dist/linux-x64/pr-merge-readiness
install -m 644 "$artifacts_dir/linux-x64/SHA256SUMS" dist/linux-x64/SHA256SUMS

git rm -r --ignore-unmatch dist/linux-arm64
git add -f dist/linux-x64/pr-merge-readiness dist/linux-x64/SHA256SUMS
if ! git diff --cached --quiet; then
  git -c user.name='github-actions[bot]' \
    -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
    commit -m "$RELEASE_VERSION の配布用バイナリを同梱する"
fi
release_sha="$(git rev-parse HEAD)"
git tag "$RELEASE_VERSION" "$release_sha"
gh auth setup-git
git push --atomic origin "$release_sha:refs/heads/main" "refs/tags/$RELEASE_VERSION"

notes="$artifacts_dir/notes.md"
cat > "$notes" <<EOF
リリース: $RELEASE_VERSION
ソースコミット: $GITHUB_SHA
配布用コミット: $release_sha
配布ブランチ: main
検証 run: ${GITHUB_SERVER_URL:-https://github.com}/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID

Linux x64 用の Python 3.14 同梱バイナリです。arm64・Windows・macOS は対象外です。
利用側の uses を配布用コミット $release_sha に固定してください。
設定 version 4 が必要です。docs/workflow.md に従って設定と workflow を同時に移行してください。
実行時に uv・Python の導入やビルドは行いません。
EOF
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  cat "$notes" >> "$GITHUB_STEP_SUMMARY"
fi
tar -C dist/linux-x64 -czf dist/pr-merge-readiness-linux-x64.tar.gz \
  pr-merge-readiness SHA256SUMS
gh release create "$RELEASE_VERSION" --repo "$GITHUB_REPOSITORY" --verify-tag \
  --title "$RELEASE_VERSION" --notes-file "$notes" \
  dist/pr-merge-readiness-linux-x64.tar.gz
