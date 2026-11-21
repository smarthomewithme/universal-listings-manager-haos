#!/usr/bin/env bash
set -euo pipefail

if [[ "$GITHUB_SERVER_URL" != https://github.com ]]; then
  echo "This workflow requires a public repository on github.com." >&2
  exit 1
fi
if [[ "$(gh api "repos/$GITHUB_REPOSITORY" --jq '.visibility + ":" + .default_branch')" != public:main ]]; then
  echo "The repository must be public and use main as its default branch." >&2
  exit 1
fi

repo="${GITHUB_REPOSITORY,,}"
image="ghcr.io/$repo"
reference="$image:$VERSION"
tag="v$VERSION"
package_url="https://github.com/users/${repo%%/*}/packages/container/package/${repo#*/}"

git fetch origin main development --tags
if [[ "$(git rev-parse origin/development)" != "$GITHUB_SHA" ]]; then
  echo "A newer commit superseded this candidate on development."
  exit 0
fi
stable="$(git rev-parse origin/main)"
stable_version=()
if git cat-file -e "$stable:olx_manager/config.yaml" 2>/dev/null; then
  git show "$stable:olx_manager/config.yaml" > "$RUNNER_TEMP/ulm-main.yaml"
  stable_version=("$(python -c 'import sys,yaml; print(yaml.safe_load(open(sys.argv[1]))["version"])' "$RUNNER_TEMP/ulm-main.yaml")")
fi
if [[ "$(python scripts/release_metadata.py check)" != "$VERSION" ]]; then
  echo "Inconsistent candidate version." >&2
  exit 1
fi
decision="$(python scripts/release_metadata.py decision "${stable_version[@]}")"
if [[ "$decision" == unchanged ]]; then
  # A retry can recreate only the GitHub Release entry for an already-promoted candidate.
  if git merge-base --is-ancestor "$GITHUB_SHA" "$stable" &&
    git cat-file -e "$tag^{commit}" 2>/dev/null &&
    ! gh release view "$tag" >/dev/null 2>&1; then
    gh release create "$tag" --verify-tag --notes-from-tag --title "Universal Listings Manager $VERSION"
  fi
  exit 0
fi
if [[ -n "$(git ls-remote origin "refs/tags/$tag")" ]]; then
  echo "Git tag $tag already exists. Release tags are immutable." >&2
  exit 1
fi

docker load -i "$RUNNER_TEMP/ulm-candidate.tar"

inspect_label() {
  docker image inspect --format "{{ index .Config.Labels \"$2\" }}" "$1"
}

assert_identity() {
  [[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$1")" == linux/amd64 ]]
  [[ "$(inspect_label "$1" org.opencontainers.image.revision)" == "$GITHUB_SHA" ]]
  [[ "$(inspect_label "$1" io.hass.version)" == "$VERSION" ]]
  [[ "$(inspect_label "$1" io.hass.arch)" == amd64 ]]
  [[ "$(inspect_label "$1" io.hass.type)" == app ]]
  [[ "$(inspect_label "$1" org.opencontainers.image.version)" == "$VERSION" ]]
  [[ "$(inspect_label "$1" org.opencontainers.image.source)" == "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" ]]
}

assert_identity ulm:candidate
printf '%s' "$GH_TOKEN" | docker login ghcr.io --username "$GITHUB_ACTOR" --password-stdin
registered="$(python scripts/ghcr_manifest.py --authenticated --allow-missing "$reference")"
if [[ -z "$registered" ]]; then
  docker tag ulm:candidate "$reference"
  docker push "$reference"
  registered="$(python scripts/ghcr_manifest.py --authenticated "$reference")"
else
  docker pull --platform=linux/amd64 "$image@$registered"
  if ! assert_identity "$image@$registered"; then
    echo "GHCR tag $reference belongs to a different revision. Bump the version to publish changed code." >&2
    exit 1
  fi
  echo "Resuming the release with existing digest $registered."
fi
docker logout ghcr.io >/dev/null

anon_config="$(mktemp -d)"
trap 'rm -rf "$anon_config"' EXIT
printf '{}\n' > "$anon_config/config.json"
if ! public_digest="$(python scripts/ghcr_manifest.py "$reference")"; then
  {
    echo "### Package is awaiting public visibility"
    echo "Set the [GHCR package]($package_url) to **Public**, then re-run the failed release job."
    echo "The main branch still advertises the previous version."
  } >> "$GITHUB_STEP_SUMMARY"
  exit 1
fi
if [[ "$registered" != "$public_digest" ]]; then
  echo "The anonymous manifest digest differs from the verified GHCR digest." >&2
  exit 1
fi
DOCKER_CONFIG="$anon_config" docker pull --platform=linux/amd64 "$reference"
assert_identity "$reference"
docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$reference" |
  grep -Fx "$image@$public_digest"
bash scripts/verify_container.sh "$image@$public_digest" "$VERSION"

# Recheck the branch tips before publishing the metadata. Git's atomic fast-forward
# push also prevents concurrent modification of main or claiming an occupied tag.
git fetch origin main development
if [[ "$(git rev-parse origin/development)" != "$GITHUB_SHA" || "$(git rev-parse origin/main)" != "$stable" ]]; then
  echo "A branch changed during publication. Home Assistant still uses the previous metadata." >&2
  exit 1
fi
python scripts/release_metadata.py render "$GITHUB_REPOSITORY" "$GITHUB_SHA" "$public_digest"
python - "$VERSION" <<'PY' > "$RUNNER_TEMP/ulm-notes.md"
import sys
from pathlib import Path
sys.path.insert(0, "scripts")
from release_metadata import notes
print(notes(Path("olx_manager/CHANGELOG.md"), sys.argv[1]))
PY
# Workflows execute from development. Keep main free of workflow updates so
# promotion requires contents:write, never access to edit workflow definitions.
git rm -r .github >/dev/null
git add README.md repository.yaml olx_manager/config.yaml RELEASE_IMAGE.md
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
promoted="$(git commit-tree "$(git write-tree)" -p "$stable" -p "$GITHUB_SHA"   -m "Release Universal Listings Manager $VERSION"   -m "Source-Commit: $GITHUB_SHA" -m "Image-Digest: $public_digest")"
git tag -a "$tag" "$promoted" -F "$RUNNER_TEMP/ulm-notes.md"
git push --atomic origin "$promoted:refs/heads/main" "refs/tags/$tag:refs/tags/$tag"
gh release create "$tag" --verify-tag --notes-from-tag --title "Universal Listings Manager $VERSION"

{
  echo "### Home Assistant release $VERSION"
  echo "- GitHub: $GITHUB_SERVER_URL/$GITHUB_REPOSITORY"
  echo "- GHCR: `$reference`"
  echo "- Digest: `$public_digest`"
  echo "- Verified the anonymous manifest, anonymous pull and a smoke test of the GHCR image."
} >> "$GITHUB_STEP_SUMMARY"
