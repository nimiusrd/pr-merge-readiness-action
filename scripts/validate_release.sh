#!/usr/bin/env bash
# main にマージ済みのタグと、そのコミットに同梱された配布物を確認する。
set -euo pipefail
[[ "$RELEASE_VERSION" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
[[ "$GITHUB_REF" == "refs/tags/$RELEASE_VERSION" ]]
release_sha="$(git rev-parse HEAD)"
test "$release_sha" = "$(git rev-parse "$GITHUB_SHA^{commit}")"
test -z "$(git status --porcelain)"

# 軽量タグと注釈付きタグの両方をコミット SHA で照合する。
remote_tag="$(git ls-remote --exit-code origin "$GITHUB_REF" "$GITHUB_REF^{}")"
remote_sha="$(awk '$2 ~ /\^\{\}$/ {print $1}' <<< "$remote_tag")"
if [[ -z "$remote_sha" ]]; then
  remote_sha="$(awk '{print $1}' <<< "$remote_tag")"
fi
test "$remote_sha" = "$release_sha"
git fetch --no-tags origin +refs/heads/main:refs/remotes/origin/main
git merge-base --is-ancestor "$release_sha" refs/remotes/origin/main

# 古い CPU の配布物や余分なファイル、symlink、実行権限の欠落を拒否する。
test "$(git ls-tree -r --name-only HEAD dist/)" = $'dist/linux-x64/SHA256SUMS\ndist/linux-x64/pr-merge-readiness'
[[ "$(git ls-tree HEAD dist/linux-x64/pr-merge-readiness)" == '100755 blob '* ]]
[[ "$(git ls-tree HEAD dist/linux-x64/SHA256SUMS)" == '100644 blob '* ]]
(
  cd dist/linux-x64
  test "$(cat SHA256SUMS)" = "$(sha256sum pr-merge-readiness)"
)
