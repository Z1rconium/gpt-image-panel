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
