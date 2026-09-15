#!/usr/bin/env bash
# 同じ run の検証済みバイナリを同梱した子コミットを、配布ブランチとタグで公開する。
set -euo pipefail
[[ "$GITHUB_REF" == refs/heads/main ]]
[[ "$RELEASE_VERSION" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
release_branch="codex/releases/$RELEASE_VERSION"
test "$(git rev-parse HEAD)" = "$GITHUB_SHA"
test -z "$(git status --porcelain)"
remote_refs="$(git ls-remote origin "refs/tags/$RELEASE_VERSION" "refs/heads/$release_branch")"
test -z "$remote_refs"

for platform in linux-x64 linux-arm64; do
  gh run download "$GITHUB_RUN_ID" --repo "$GITHUB_REPOSITORY" \
    --name "binary-$platform" --dir "dist/$platform"
  (cd "dist/$platform" && sha256sum --check SHA256SUMS)
  chmod 755 "dist/$platform/pr-merge-readiness"
done

git add -f dist/linux-x64/pr-merge-readiness dist/linux-x64/SHA256SUMS \
  dist/linux-arm64/pr-merge-readiness dist/linux-arm64/SHA256SUMS
git -c user.name='github-actions[bot]' \
  -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
  commit -m "$RELEASE_VERSION の配布用バイナリを同梱する"
release_sha="$(git rev-parse HEAD)"
git tag "$RELEASE_VERSION" "$release_sha"
gh auth setup-git
git push --atomic origin "$release_sha:refs/heads/$release_branch" "refs/tags/$RELEASE_VERSION"

notes="$(mktemp)"
trap 'rm -f "$notes"' EXIT
cat > "$notes" <<EOF
ソースコミット: $GITHUB_SHA
配布用コミット: $release_sha
配布ブランチ: $release_branch

Linux x64 / arm64 用の Python 3.14 同梱バイナリです。
利用側の uses と TOML の action_ref を、配布用コミット $release_sha に揃えてください。
実行時に uv・Python の導入やビルドは行いません。
EOF
for platform in linux-x64 linux-arm64; do
  tar -C "dist/$platform" -czf "dist/pr-merge-readiness-$platform.tar.gz" \
    pr-merge-readiness SHA256SUMS
done
gh release create "$RELEASE_VERSION" --repo "$GITHUB_REPOSITORY" --verify-tag \
  --title "$RELEASE_VERSION" --notes-file "$notes" \
  dist/pr-merge-readiness-linux-x64.tar.gz dist/pr-merge-readiness-linux-arm64.tar.gz
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  cat "$notes" >> "$GITHUB_STEP_SUMMARY"
fi
