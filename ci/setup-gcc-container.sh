#!/usr/bin/env bash
set -euo pipefail
: "${RUNNER_TEMP:?Set RUNNER_TEMP}"
: "${GITHUB_PATH:?Set GITHUB_PATH}"
: "${GITHUB_ENV:?Set GITHUB_ENV}"

gcc_version=$(g++ -dumpfullversion)
if [[ "$gcc_version" != 16.2.* ]]; then
  echo "Expected GCC 16.2.x, found $gcc_version" >&2
  exit 1
fi
g++ --version
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
  ca-certificates cmake git libjemalloc-dev ninja-build python3 python3-venv
python3 -m venv "$RUNNER_TEMP/parlay-python"
echo "$RUNNER_TEMP/parlay-python/bin" >> "$GITHUB_PATH"
{
  echo 'CC=gcc'
  echo 'CXX=g++'
} >> "$GITHUB_ENV"
