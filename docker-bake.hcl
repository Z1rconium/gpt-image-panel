// Release build definition. See AGENTS.md ("Multi-Architecture GHCR Release").
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
}

// Multi-arch image pushed to GHCR (add --push on the command line).
target "release" {
  inherits   = ["_common"]
  platforms  = ["linux/amd64", "linux/arm64"]
  tags       = ["${IMAGE}:${TAG}"]
  cache-from = ["type=registry,ref=${CACHE_IMAGE}"]
  cache-to = equal(EXPORT_CACHE, "true") ? [
    "type=registry,ref=${CACHE_IMAGE},mode=max,compression=zstd,oci-mediatypes=true,image-manifest=true",
  ] : []
}

// Single-platform image loaded into the local daemon for smoke testing:
//   docker buildx bake --load local            (amd64 host)
//   docker buildx bake --load --set local.platform=linux/arm64 local
target "local" {
  inherits   = ["_common"]
  platforms  = ["linux/amd64"]
  tags       = ["gpt-image-panel:${TAG}"]
  cache-from = ["type=registry,ref=${CACHE_IMAGE}"]
}
