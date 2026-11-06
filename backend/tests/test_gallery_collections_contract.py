from PIL import Image

from backend.tests.support.contract import *  # noqa: F403


def _create_collection(client, name: str) -> dict:
    resp = client.post("/api/gallery/collections", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_gallery_collections_crud_default_and_order(client):
    assert client.get("/api/gallery/collections").json() == []

    travel = _create_collection(client, "  Travel   picks ")
    assert travel["name"] == "Travel picks"
    assert travel["position"] == 0
    assert travel["is_default"] is False
    portraits = _create_collection(client, "Portraits")
    assert portraits["position"] == 1

    duplicate = client.post("/api/gallery/collections", json={"name": "travel PICKS"})
    assert duplicate.status_code == 409
    assert client.post("/api/gallery/collections", json={"name": "   "}).status_code == 422
    assert client.post("/api/gallery/collections", json={"name": "x" * 61}).status_code == 422

    renamed = client.patch(f"/api/gallery/collections/{travel['id']}", json={"name": "Travel"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Travel"
    conflict = client.patch(f"/api/gallery/collections/{portraits['id']}", json={"name": "travel"})
    assert conflict.status_code == 409

    assert client.patch(f"/api/gallery/collections/{travel['id']}", json={"is_default": True}).json()["is_default"]
    assert client.patch(f"/api/gallery/collections/{portraits['id']}", json={"is_default": True}).json()["is_default"]
    defaults = [item["id"] for item in client.get("/api/gallery/collections").json() if item["is_default"]]
    assert defaults == [portraits["id"]]

    partial = client.put("/api/gallery/collections/order", json={"ids": [portraits["id"]]})
    assert partial.status_code == 422
    unknown = client.put("/api/gallery/collections/order", json={"ids": [portraits["id"], "missing"]})
    assert unknown.status_code == 422
    reordered = client.put(
        "/api/gallery/collections/order",
        json={"ids": [portraits["id"], travel["id"]]},
    )
    assert reordered.status_code == 200
    assert [item["id"] for item in reordered.json()] == [portraits["id"], travel["id"]]
    assert [item["id"] for item in client.get("/api/gallery/collections").json()] == [
        portraits["id"],
        travel["id"],
    ]

    assert client.patch("/api/gallery/collections/missing", json={"name": "Nope"}).status_code == 404
    assert client.delete("/api/gallery/collections/missing").status_code == 404


def test_gallery_collection_items_filter_and_cascade(client):
    _fake_gallery_entry("col-1", "sunset beach", "1024x1024", "col-1.png")
    _fake_gallery_entry("col-2", "sunset mountain", "1024x1024", "col-2.png")
    _fake_gallery_entry("col-3", "city night", "1024x1024", "col-3.png")
    album = _create_collection(client, "Sunsets")

    added = client.post(
        f"/api/gallery/collections/{album['id']}/items",
        json={"ids": ["col-1", "col-3", "missing-image"]},
    )
    assert added.status_code == 200
    assert added.json()["changed_count"] == 2
    assert added.json()["collection"]["image_count"] == 2
    assert added.json()["collection"]["cover_image_id"] == "col-3"
    assert added.json()["collection"]["cover_filename"] == "col-3.png"

    again = client.post(f"/api/gallery/collections/{album['id']}/items", json={"ids": ["col-1"]})
    assert again.json()["changed_count"] == 0

    search = client.post("/api/gallery/search", json={"collection_id": album["id"]})
    assert search.status_code == 200
    assert {image["id"] for image in search.json()["images"]} == {"col-1", "col-3"}
    assert search.json()["total"] == 2
    listing = client.get("/api/gallery", params={"collection_id": album["id"]})
    assert listing.json()["total"] == 2

    token = client.post(
        "/api/gallery/batch/selection-tokens",
        json={"filters": {"prompt": "sunset"}},
    ).json()["selection_token"]
    by_token = client.post(
        f"/api/gallery/collections/{album['id']}/items",
        json={"selection_token": token},
    )
    assert by_token.json()["changed_count"] == 1
    assert by_token.json()["collection"]["image_count"] == 3

    membership = client.get("/api/gallery/col-2/collections")
    assert membership.status_code == 200
    assert membership.json() == {"image_id": "col-2", "collection_ids": [album["id"]]}
    assert client.get("/api/gallery/missing-image/collections").status_code == 404

    removed = client.post(
        f"/api/gallery/collections/{album['id']}/items/remove",
        json={"ids": ["col-3"]},
    )
    assert removed.json()["changed_count"] == 1
    removed_by_token = client.post(
        f"/api/gallery/collections/{album['id']}/items/remove",
        json={"selection_token": token},
    )
    assert removed_by_token.json()["changed_count"] == 2
    assert removed_by_token.json()["collection"]["image_count"] == 0

    client.post(f"/api/gallery/collections/{album['id']}/items", json={"ids": ["col-1", "col-2"]})
    assert client.delete("/api/gallery/col-1").status_code == 200
    remaining = client.post("/api/gallery/search", json={"collection_id": album["id"]}).json()
    assert [image["id"] for image in remaining["images"]] == ["col-2"]

    assert client.delete(f"/api/gallery/collections/{album['id']}").status_code == 204
    assert gallery_queries.get_gallery_entry("col-2") is not None
    assert client.get("/api/gallery/col-2/collections").json()["collection_ids"] == []
    assert client.post(
        f"/api/gallery/collections/{album['id']}/items",
        json={"ids": ["col-2"]},
    ).status_code == 404


def test_gallery_collection_export_job_zips_members(client):
    _fake_gallery_entry("zip-col-1", "one", "1024x1024", "zip-col-1.png")
    _fake_gallery_entry("zip-col-2", "two", "1024x1024", "zip-col-2.png")
    album = _create_collection(client, "Best / of \"2026\"")

    empty = client.post("/api/gallery/export-jobs", json={"collection_id": album["id"]})
    assert empty.status_code == 404
    missing = client.post("/api/gallery/export-jobs", json={"collection_id": "missing"})
    assert missing.status_code == 404
    both = client.post(
        "/api/gallery/export-jobs",
        json={"collection_id": album["id"], "ids": ["zip-col-1"]},
    )
    assert both.status_code == 422

    client.post(f"/api/gallery/collections/{album['id']}/items", json={"ids": ["zip-col-2"]})
    created = client.post("/api/gallery/export-jobs", json={"collection_id": album["id"]})
    assert created.status_code == 202
    finished = _wait_for_gallery_export_job(client, created.json()["job_id"])
    assert finished["status"] == "success"
    assert finished["requested_count"] == 1

    archive = client.get(finished["download_url"])
    assert archive.status_code == 200
    disposition = archive.headers["content-disposition"]
    assert "gpt-images-Best-of-2026-" in disposition
    assert '"2026"' not in disposition
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "images/zip-col-2.png" in zf.namelist()
        assert "images/zip-col-1.png" not in zf.namelist()
        metadata = json.loads(zf.read("metadata.json"))
    assert metadata["collection"] == {"id": album["id"], "name": album["name"]}
    assert [image["id"] for image in metadata["images"]] == ["zip-col-2"]


def test_gallery_collection_assets_and_references(client):
    album = _create_collection(client, "Alice Character")
    assert album["asset_alias"] is None

    # Invalid alias validations
    assert client.patch(f"/api/gallery/collections/{album['id']}", json={"asset_alias": "alice bot"}).status_code == 422
    assert client.patch(f"/api/gallery/collections/{album['id']}", json={"asset_alias": "alice@character"}).status_code == 422
    assert client.patch(f"/api/gallery/collections/{album['id']}", json={"asset_alias": "a" * 33}).status_code == 422
    assert client.patch(f"/api/gallery/collections/{album['id']}", json={"asset_alias": "round-1"}).status_code == 422
    assert client.patch(f"/api/gallery/collections/{album['id']}", json={"asset_alias": "round-2-image-1"}).status_code == 422
    assert client.patch(f"/api/gallery/collections/{album['id']}", json={"asset_alias": "第1轮图1"}).status_code == 422
    assert client.patch(f"/api/gallery/collections/{album['id']}", json={"asset_alias": "1轮图2"}).status_code == 422

    # Valid alias with Unicode
    patched = client.patch(f"/api/gallery/collections/{album['id']}", json={"asset_alias": "艾莉丝_01"})
    assert patched.status_code == 200
    assert patched.json()["asset_alias"] == "艾莉丝_01"

    # Valid English alias
    patched = client.patch(f"/api/gallery/collections/{album['id']}", json={"asset_alias": "alice-v1"})
    assert patched.status_code == 200
    assert patched.json()["asset_alias"] == "alice-v1"

    # Case-insensitive collision
    bob = _create_collection(client, "Bob Character")
    conflict = client.patch(f"/api/gallery/collections/{bob['id']}", json={"asset_alias": "ALICE-V1"})
    assert conflict.status_code == 409

    # Clear alias with null
    cleared = client.patch(f"/api/gallery/collections/{album['id']}", json={"asset_alias": None})
    assert cleared.status_code == 200
    assert cleared.json()["asset_alias"] is None

    # Set alias and verify omitting field preserves it
    patched = client.patch(f"/api/gallery/collections/{album['id']}", json={"asset_alias": "alice"})
    assert patched.json()["asset_alias"] == "alice"
    preserved = client.patch(f"/api/gallery/collections/{album['id']}", json={"name": "Alice Character Pack"})
    assert preserved.status_code == 200
    assert preserved.json()["asset_alias"] == "alice"
    assert preserved.json()["name"] == "Alice Character Pack"

    # References endpoint 404 for missing collection
    assert client.get("/api/gallery/collections/missing-col/references").status_code == 404

    # Prepare entries: 2 PNGs, 1 SVG, 1 GIF
    _fake_gallery_entry("ref-1", "alice front", "1024x1024", "ref-1.png")
    _fake_gallery_entry("ref-2", "alice side", "1024x1024", "ref-2.png")

    svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024"><circle cx="512" cy="512" r="300" fill="coral"/></svg>'
    gallery_mutations.add_to_gallery_sync(
        image_id="ref-3",
        prompt="alice vector",
        size="1024x1024",
        filename="ref-3.svg",
        metadata={
            "model": "gpt-image-2",
            "quality": "auto",
            "output_format": "svg",
            "n": 1,
            "api_path": "/v1/images/generations",
            "api_preset_name": "Default",
        },
        image_bytes=svg_bytes,
    )

    out = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(
        out, "GIF", save_all=True, append_images=[Image.new("RGB", (32, 32), "blue")],
        duration=[100, 200], loop=0,
    )
    gif_bytes = out.getvalue()
    gallery_mutations.add_to_gallery_sync(
        image_id="ref-4",
        prompt="alice animated",
        size="1024x1024",
        filename="ref-4.gif",
        metadata={
            "model": "gpt-image-2",
            "quality": "auto",
            "output_format": "gif",
            "n": 1,
            "api_path": "/v1/images/generations",
            "api_preset_name": "Default",
        },
        image_bytes=gif_bytes,
    )

    client.post(
        f"/api/gallery/collections/{album['id']}/items",
        json={"ids": ["ref-1", "ref-2", "ref-3", "ref-4"]},
    )

    refs_resp = client.get(f"/api/gallery/collections/{album['id']}/references")
    assert refs_resp.status_code == 200
    refs_data = refs_resp.json()
    assert refs_data["collection"]["id"] == album["id"]
    assert refs_data["collection"]["asset_alias"] == "alice"
    assert refs_data["total_count"] == 4
    assert refs_data["skipped_unsupported"] == 2
    assert [img["id"] for img in refs_data["images"]] == ["ref-1", "ref-2"]

    # Items order endpoint
    assert client.put("/api/gallery/collections/missing-col/items/order", json={"ids": ["ref-1"]}).status_code == 404
    assert client.put(f"/api/gallery/collections/{album['id']}/items/order", json={"ids": ["ref-1"]}).status_code == 422
    assert client.put(f"/api/gallery/collections/{album['id']}/items/order", json={"ids": ["ref-1", "ref-2", "ref-3", "ref-4", "extra"]}).status_code == 422
    assert client.put(f"/api/gallery/collections/{album['id']}/items/order", json={"ids": ["ref-1", "ref-1", "ref-2", "ref-3"]}).status_code == 422

    # Reorder members: ref-2, ref-4, ref-3, ref-1
    reorder_resp = client.put(
        f"/api/gallery/collections/{album['id']}/items/order",
        json={"ids": ["ref-2", "ref-4", "ref-3", "ref-1"]},
    )
    assert reorder_resp.status_code == 200

    # References endpoint now reflects ref-2 before ref-1
    refs_after_order = client.get(f"/api/gallery/collections/{album['id']}/references").json()
    assert [img["id"] for img in refs_after_order["images"]] == ["ref-2", "ref-1"]

    # Adding a new member ref-5 without explicit position places it at the end
    _fake_gallery_entry("ref-5", "alice back", "1024x1024", "ref-5.png")
    client.post(f"/api/gallery/collections/{album['id']}/items", json={"ids": ["ref-5"]})

    refs_with_new = client.get(f"/api/gallery/collections/{album['id']}/references").json()
    assert refs_with_new["total_count"] == 5
    assert refs_with_new["skipped_unsupported"] == 2
    assert [img["id"] for img in refs_with_new["images"]] == ["ref-2", "ref-1", "ref-5"]

