// Release build definition. See AGENTS.md ("Multi-Architecture GHCR Release").
//
// CI (.github/workflows/release-image.yml) builds each platform natively on its
// own runner through the release-<arch> targets, pushes the results by digest
// and merges them into ${IMAGE}:${TAG} afterwards. A local release uses the
// multi-platform release target on the gpt-image-linux-builder created by
// deploy/buildx-builder.sh:
//
//   TAG=$(tr -d '\n' < VERSION) VCS_REF=$(git rev-parse HEAD) \
//     docker buildx bake --builder gpt-image-linux-builder --push release
//
// EXPORT_CACHE=false skips the registry cache export when the builder's local
// cache is already warm (it only pays off after `buildx prune` or on a new host).

variable "IMAGE" {
  default = "ghcr.io/z1rconium/gpt-image-linux"
}

variable "CACHE_IMAGE" {
  default = "ghcr.io/z1rconium/gpt-image-linux-build-cache"
}

variable "TAG" {
  default = "dev"
}

variable "VCS_REF" {
  default = "unknown"
}

variable "EXPORT_CACHE" {
  default = "true"
}

target "_common" {
  context    = "."
  dockerfile = "Dockerfile"
  args = {
    APP_VERSION = TAG
    VCS_REF     = VCS_REF
  }
  # Attestation manifests would show up as unknown/unknown platforms on GHCR.
  attest = [
    "type=provenance,disabled=true",
    "type=sbom,disabled=true",
  ]
  # Registry cache layout: the multi-platform release target writes both
  # platforms into ${CACHE_IMAGE}; the per-platform CI targets each write
  # ${CACHE_IMAGE}:<arch>. Every target imports all three so a local release
  # after a CI run (or the other way round) still starts warm. A ref that
  # does not exist yet logs a "not found" importer error but does not fail
  # the build.
  cache-from = [
    "type=registry,ref=${CACHE_IMAGE}",
    "type=registry,ref=${CACHE_IMAGE}:amd64",
    "type=registry,ref=${CACHE_IMAGE}:arm64",
  ]
}

// Multi-arch image pushed to GHCR from one builder (add --push on the command line).
target "release" {
  inherits  = ["_common"]
  platforms = ["linux/amd64", "linux/arm64"]
  tags      = ["${IMAGE}:${TAG}"]
  cache-to = equal(EXPORT_CACHE, "true") ? [
    "type=registry,ref=${CACHE_IMAGE},mode=max,compression=zstd,oci-mediatypes=true,image-manifest=true,ignore-error=true",
  ] : []
}

// One native single-platform build per CI runner: release-amd64 and
// release-arm64. The image is pushed straight from BuildKit by digest (no tag,
// no local tarball); the workflow reads containerimage.digest from the bake
// metadata and assembles the manifest list with `buildx imagetools create`.
// Do not pass --push here: it would replace the output with a tagged push.
target "release-platform" {
  name      = "release-${arch}"
  matrix    = { arch = ["amd64", "arm64"] }
  inherits  = ["_common"]
  platforms = ["linux/${arch}"]
  output    = ["type=image,name=${IMAGE},push-by-digest=true,name-canonical=true,push=true"]
  cache-to = equal(EXPORT_CACHE, "true") ? [
    "type=registry,ref=${CACHE_IMAGE}:${arch},mode=max,compression=zstd,oci-mediatypes=true,image-manifest=true,ignore-error=true",
  ] : []
}

// Single-platform image loaded into the local daemon for smoke testing:
//   docker buildx bake --load local            (amd64 host)
//   docker buildx bake --load --set local.platform=linux/arm64 local
target "local" {
  inherits  = ["_common"]
  platforms = ["linux/amd64"]
  tags      = ["gpt-image-panel:${TAG}"]
}
