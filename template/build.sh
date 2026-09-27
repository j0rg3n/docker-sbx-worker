#!/usr/bin/env bash
# Build the dev template with the host Docker Engine and load it into sbx.
# sbx docs say template *builds* need Docker Desktop; `sbx template load` from a tar
# is the route that avoids it. The tag comes from the tar (docker save records it).
set -euo pipefail
TAG="${1:-sbx-worker-dev:1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker build -t "$TAG" "$HERE"
TAR="$(mktemp --suffix=.tar)"
trap 'rm -f "$TAR"' EXIT
docker save "$TAG" -o "$TAR"
sbx template load "$TAR"
echo "Loaded $TAG. Set agent.template = \"$TAG\" in the project config."
