#!/usr/bin/env sh
# Create or re-create the persistent buildx builder used for GHCR releases.
# See AGENTS.md ("Multi-Architecture GHCR Release").
#
#   deploy/buildx-builder.sh                                    # local node only
#   REMOTE_ENDPOINT=ssh://user@arm64-host deploy/buildx-builder.sh   # + native node
#
# The local node is created with deploy/buildkitd.toml (raised cache-mount GC
# limits) and pinned to the host's own platform. REMOTE_ENDPOINT appends a
# second docker-container node for the other architecture (REMOTE_PLATFORM,
# default: whichever of linux/amd64 and linux/arm64 the host is not), so
# `docker buildx bake release` builds both platforms natively instead of
# emulating one under QEMU. The endpoint is any Docker endpoint the CLI can
# reach (ssh://user@host, tcp://host:2376, or a docker context name).
#
# Re-creation keeps the BuildKit state volumes (docker buildx rm --keep-state),
# so the local layer cache and RUN --mount=type=cache directories survive.
set -eu

BUILDER=${BUILDER:-gpt-image-linux-builder}
REMOTE_ENDPOINT=${REMOTE_ENDPOINT:-}
here=$(cd "$(dirname "$0")" && pwd)
config="$here/buildkitd.toml"
flags='--allow-insecure-entitlement=network.host'

case "$(docker info --format '{{.Architecture}}')" in
  x86_64 | amd64) local_platform=linux/amd64; other_platform=linux/arm64 ;;
  aarch64 | arm64) local_platform=linux/arm64; other_platform=linux/amd64 ;;
  *) echo "unsupported host architecture: $(docker info --format '{{.Architecture}}')" >&2; exit 1 ;;
esac
REMOTE_PLATFORM=${REMOTE_PLATFORM:-$other_platform}

if docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  echo "Re-creating builder $BUILDER (BuildKit state volumes are kept)"
  docker buildx rm --keep-state "$BUILDER"
fi

docker buildx create --name "$BUILDER" --driver docker-container \
  --platform "$local_platform" \
  --buildkitd-config "$config" --buildkitd-flags "$flags"

if [ -n "$REMOTE_ENDPOINT" ]; then
  docker buildx create --append --name "$BUILDER" --node "${BUILDER}-remote" \
    --driver docker-container --platform "$REMOTE_PLATFORM" \
    --buildkitd-config "$config" --buildkitd-flags "$flags" \
    "$REMOTE_ENDPOINT"
fi

docker buildx inspect --bootstrap "$BUILDER"
