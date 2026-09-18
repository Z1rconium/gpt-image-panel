MAX_EDIT_SOURCE_IMAGES = 16
EDIT_MULTIPART_METADATA_OVERHEAD_BYTES = 1024 * 1024
# The upstream /v1/images/edits contract requires masks to be PNG files under
# 4 MB with dimensions equal to the first image, so this cap is independent of
# MAX_FILE_SIZE_MB.
MAX_EDIT_MASK_BYTES = 4 * 1024 * 1024
EDIT_MASK_FIELD_NAME = "mask"
