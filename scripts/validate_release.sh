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

# 配布物はバンドル済み JS・module 宣言・checksum だけ。symlink は拒否する。
test "$(git ls-tree -r --name-only HEAD dist/)" = $'dist/SHA256SUMS\ndist/index.js\ndist/package.json'
for file in SHA256SUMS index.js package.json; do
  [[ "$(git ls-tree HEAD "dist/$file")" == '100644 blob '* ]]
done
(
  cd dist
  test "$(cat SHA256SUMS)" = "$(sha256sum index.js package.json)"
)
