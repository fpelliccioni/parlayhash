#!/usr/bin/env bash
set -euo pipefail
# Only release/X.Y.Z and hotfix/X.Y.Z pushes publish; tags are records.
if [[ ${GITHUB_EVENT_NAME:-} != push || ! ${GITHUB_REF:-} =~ ^refs/heads/(release|hotfix)/([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  echo 'Publication requires a release/X.Y.Z or hotfix/X.Y.Z branch push.' >&2
  exit 1
fi
version=${BASH_REMATCH[2]}
: "${CONAN_LOGIN_USERNAME:?Missing package-server username secret}"
: "${CONAN_PASSWORD:?Missing package-server password secret}"
conan profile detect --force
conan remote add kth https://packages.kth.cash/api/ --force
conan create . --version "$version" -pr:h ci/conan-cpp23 -pr:b default
conan list "parlayhash/$version#latest" --format=json > recipe.json
reference=$(python3 - "$version" <<'PY'
import json, sys
ref = 'parlayhash/' + sys.argv[1]
data = json.load(open('recipe.json'))
revisions = data['Local Cache'][ref]['revisions']
if len(revisions) != 1:
    raise SystemExit('Expected exactly one recipe revision')
print(ref + '#' + next(iter(revisions)))
PY
)
conan remote login kth "$CONAN_LOGIN_USERNAME" -p "$CONAN_PASSWORD"
conan upload "$reference" -r kth -c
# Verify the exact uploaded recipe and binary from an empty, anonymous cache.
export CONAN_HOME
CONAN_HOME=$(mktemp -d)
trap 'rm -rf "$CONAN_HOME"' EXIT
# Include remote-specific variants as well as the global credential variables.
for credential in ${!CONAN_LOGIN_USERNAME@} ${!CONAN_PASSWORD@}; do
  unset "$credential"
done
conan profile detect --force
conan remote add kth https://packages.kth.cash/api/
conan install --requires="$reference" -pr:h ci/conan-cpp23 -pr:b default --build=never -r kth -cc core:non_interactive=True
conan test test_package "$reference" -pr:h ci/conan-cpp23 -pr:b default --build=never -cc core:non_interactive=True
