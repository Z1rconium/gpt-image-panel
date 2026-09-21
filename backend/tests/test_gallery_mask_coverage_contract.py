from backend.tests.support.contract import *  # noqa: F403


def _seed_entry(image_id: str, metadata: dict):
    gallery_mutations.add_to_gallery_sync(
        image_id=image_id,
        prompt="seed image",
        size="1024x1024",
        filename=f"{image_id}.png",
        metadata=metadata,
        image_bytes=PNG_BYTES,
    )


def test_gallery_records_mask_coverage_and_filters_masked_edits(client):
    masked_id = "gallery-masked-coverage"
    plain_id = "gallery-plain-coverage"
    _seed_entry(
        masked_id,
        {
            "model": "gpt-image-2",
            "output_format": "png",
            "n": 1,
            "api_path": "/v1/images/edits",
            "api_preset_name": "Default",
            "mask_coverage": 0.25,
        },
    )
    _seed_entry(
        plain_id,
        {
            "model": "gpt-image-2",
            "output_format": "png",
            "n": 1,
            "api_path": "/v1/images/generations",
            "api_preset_name": "Default",
        },
    )

    # The float survives the column whitelist instead of being stringified.
    assert gallery_queries.get_gallery_entry(masked_id).mask_coverage == 0.25
    assert gallery_queries.get_gallery_entry(plain_id).mask_coverage is None

    response = client.post(
        "/api/gallery/search",
        json={"mask_only": True, "page": 1, "page_size": 20},
    )
    assert response.status_code == 200
    images = response.json()["images"]
    ids = {image["id"] for image in images}
    assert masked_id in ids
    assert plain_id not in ids
    masked_entry = next(image for image in images if image["id"] == masked_id)
    assert masked_entry["mask_coverage"] == 0.25


def test_gallery_metadata_records_mask_coverage():
    from backend.app.integrations.upstream.payloads import build_gallery_metadata
    from backend.app.schemas.generation import GenerateRequest

    payload = GenerateRequest(prompt="x", model="gpt-image-2")
    without = build_gallery_metadata(payload, "/v1/images/edits", "Default")
    assert "mask_coverage" not in without

    with_coverage = build_gallery_metadata(
        payload,
        "/v1/images/edits",
        "Default",
        mask_coverage=0.125,
    )
    assert with_coverage["mask_coverage"] == 0.125
