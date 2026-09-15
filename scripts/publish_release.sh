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

for platform in linux-x64 linux-arm64; do
  gh run download "$GITHUB_RUN_ID" --repo "$GITHUB_REPOSITORY" \
    --name "binary-$platform" --dir "$artifacts_dir/$platform"
  (cd "$artifacts_dir/$platform" && sha256sum --check SHA256SUMS)
  install -D -m 755 "$artifacts_dir/$platform/pr-merge-readiness" "dist/$platform/pr-merge-readiness"
  install -m 644 "$artifacts_dir/$platform/SHA256SUMS" "dist/$platform/SHA256SUMS"
done

git add -f dist/linux-x64/pr-merge-readiness dist/linux-x64/SHA256SUMS \
  dist/linux-arm64/pr-merge-readiness dist/linux-arm64/SHA256SUMS
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
ソースコミット: $GITHUB_SHA
配布用コミット: $release_sha
配布ブランチ: main

Linux x64 / arm64 用の Python 3.14 同梱バイナリです。
利用側の uses を配布用コミット $release_sha に固定してください。
TOML の action_ref は不要です。既存設定に残っていても参照せず、設定 version と内容を検証します。
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
