#!/usr/bin/env bash
# Build the dev template with the host Docker Engine and load it into sbx.
# sbx docs say template *builds* need Docker Desktop; `sbx template load` from a tar
# is the route that avoids it. Untested on this machine yet.
set -euo pipefail
TAG="${1:-sbx-worker-dev:1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker build -t "$TAG" "$HERE"
TAR="$(mktemp --suffix=.tar)"
trap 'rm -f "$TAR"' EXIT
docker save "$TAG" -o "$TAR"
sbx template load "$TAR" "$TAG"
echo "Loaded $TAG. Set agent.template = \"$TAG\" in the project config."
