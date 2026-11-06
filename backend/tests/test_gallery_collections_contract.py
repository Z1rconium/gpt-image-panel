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



def test_asset_archive_roundtrip_conflicts_and_order(client):
    _fake_gallery_entry('archive-one', 'first', '1024x1024', 'archive-one.png')
    _fake_gallery_entry('archive-two', 'second', '1024x1024', 'archive-two.png')
    pack = _create_collection(client, 'Archive Pack')
    assert client.patch(f"/api/gallery/collections/{pack['id']}", json={'asset_alias': 'A' * 32}).status_code == 200
    assert client.post(f"/api/gallery/collections/{pack['id']}/items", json={'ids': ['archive-one', 'archive-two']}).status_code == 200
    assert client.put(f"/api/gallery/collections/{pack['id']}/items/order", json={'ids': ['archive-two', 'archive-one']}).status_code == 200
    _create_collection(client, 'Empty')

    created = client.post('/api/gallery/export-jobs', json={})
    finished = _wait_for_gallery_export_job(client, created.json()['job_id'])
    assert finished['status'] == 'success', finished
    archive = client.get(finished['download_url']).content
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        metadata = json.loads(zf.read('metadata.json'))
    saved = next(c for c in metadata['collections'] if c['id'] == pack['id'])
    assert saved['asset_alias'] == 'A' * 32
    assert saved['image_ids'] == ['archive-two', 'archive-one']
    assert any(c['name'] == 'Empty' and c['image_ids'] == [] for c in metadata['collections'])

    resp = _post_import_archive(client, archive)
    assert resp.status_code == 202, resp.text
    imported = _wait_for_gallery_import_job(client, resp.json()['job_id'])
    assert imported['status'] == 'success', imported
    collections = client.get('/api/gallery/collections').json()
    restored = next(c for c in collections if c['name'] == 'Archive Pack (1)')
    assert restored['asset_alias'] == 'A' * 30 + '_1'
    assert any(c['name'] == 'Empty (1)' for c in collections)
    refs = client.get(f"/api/gallery/collections/{restored['id']}/references").json()['images']
    assert [r['prompt'] for r in refs] == ['second', 'first']
    assert all(r['id'] not in {'archive-one', 'archive-two'} for r in refs)
    original = client.get(f"/api/gallery/collections/{pack['id']}/references").json()['images']
    assert [r['id'] for r in original] == ['archive-two', 'archive-one']


def test_collection_archive_skips_missing_members_and_keeps_other_collections_unchanged(client):
    _fake_gallery_entry('present', 'present', '1024x1024', 'present.png')
    _fake_gallery_entry('missing', 'missing', '1024x1024', 'missing.png')
    pack = _create_collection(client, 'Partial')
    client.patch(f"/api/gallery/collections/{pack['id']}", json={'asset_alias': 'partial'})
    client.post(f"/api/gallery/collections/{pack['id']}/items", json={'ids': ['present', 'missing']})
    media.safe_image_path('missing.png').unlink()
    job = client.post('/api/gallery/export-jobs', json={'collection_id': pack['id']}).json()
    finished = _wait_for_gallery_export_job(client, job['job_id'])
    archive = client.get(finished['download_url']).content
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        metadata = json.loads(zf.read('metadata.json'))
    assert metadata['collections'][0]['image_ids'] == ['present']
    resp = _post_import_archive(client, archive)
    assert _wait_for_gallery_import_job(client, resp.json()['job_id'])['status'] == 'success'
    restored = next(c for c in client.get('/api/gallery/collections').json() if c['name'] == 'Partial (1)')
    assert restored['image_count'] == 1


def test_import_collection_invalid_metadata_fails_before_images(client):
    metadata = {'images': [{'id': 'invalid-collection-image', 'filename': 'x.png', 'prompt': '', 'size': 'auto'}],
                'collections': [{'name': 'Invalid', 'asset_alias': 'bad alias', 'image_ids': ['invalid-collection-image']}]}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('metadata.json', json.dumps(metadata))
        zf.writestr('images/x.png', PNG_BYTES)
    response = _post_import_archive(client, archive.getvalue())
    assert response.status_code == 202
    assert _wait_for_gallery_import_job(client, response.json()['job_id'])['status'] == 'error'
    assert gallery_queries.get_gallery_entry('invalid-collection-image') is None
    assert client.get('/api/gallery/collections').json() == []


def test_json_only_collection_import_resolves_unicode_casefold_conflict(client):
    existing = _create_collection(client, 'Existing')
    assert client.patch(f"/api/gallery/collections/{existing['id']}", json={'asset_alias': 'ÄBC'}).status_code == 200
    metadata = {'images': [{'id': 'unicode-import', 'filename': 'x.png', 'prompt': 'x', 'size': 'auto'}],
                'collections': [{'name': 'Existing', 'asset_alias': 'äbc', 'image_ids': ['unicode-import', 'not-in-archive']}]}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('metadata.json', json.dumps(metadata))
        zf.writestr('images/x.png', PNG_BYTES)
    response = _post_import_archive(client, archive.getvalue())
    result = _wait_for_gallery_import_job(client, response.json()['job_id'])
    assert result['status'] == 'success', result
    restored = next(c for c in client.get('/api/gallery/collections').json() if c['name'] == 'Existing (1)')
    assert restored['asset_alias'] == 'äbc_1'
    assert restored['image_count'] == 1


def test_empty_asset_only_archive_roundtrip(client):
    pack = _create_collection(client, 'Empty Asset')
    assert client.patch(f"/api/gallery/collections/{pack['id']}", json={'asset_alias': 'empty'}).status_code == 200
    for request in ({}, {'collection_id': pack['id']}):
        created = client.post('/api/gallery/export-jobs', json=request)
        assert created.status_code == 202, created.text
        finished = _wait_for_gallery_export_job(client, created.json()['job_id'])
        assert finished['status'] == 'success', finished
        archive = client.get(finished['download_url']).content
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            metadata = json.loads(zf.read('metadata.json'))
        assert metadata['images'] == []
        assert any(c['asset_alias'] == 'empty' and not c['image_ids'] for c in metadata['collections'])
        response = _post_import_archive(client, archive)
        assert response.status_code == 202, response.text
        assert _wait_for_gallery_import_job(client, response.json()['job_id'])['status'] == 'success'
    restored = client.get('/api/gallery/collections').json()
    assert {c['asset_alias'] for c in restored} == {'empty', 'empty_1', 'empty_2'}

    direct = client.post('/api/gallery/direct-export-jobs')
    assert direct.status_code == 202, direct.text
    assert client.get('/api/download-all', params={'export_job_id': direct.json()['job_id']}).status_code == 200


def test_archive_default_preserved_without_overriding_target(client):
    _fake_gallery_entry('default-image', 'default', '1024x1024', 'default-image.png')
    original = _create_collection(client, 'Default pack')
    client.patch(f"/api/gallery/collections/{original['id']}", json={'asset_alias': 'default_pack', 'is_default': True})
    client.post(f"/api/gallery/collections/{original['id']}/items", json={'ids': ['default-image']})
    created = client.post('/api/gallery/export-jobs', json={}).json()
    finished = _wait_for_gallery_export_job(client, created['job_id'])
    archive = client.get(finished['download_url']).content
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert json.loads(zf.read('metadata.json'))['collections'][0]['is_default'] is True
    response = _post_import_archive(client, archive)
    assert _wait_for_gallery_import_job(client, response.json()['job_id'])['status'] == 'success'
    collections = client.get('/api/gallery/collections').json()
    assert [c['id'] for c in collections if c['is_default']] == [original['id']]
    client.patch(f"/api/gallery/collections/{original['id']}", json={'is_default': False})
    response = _post_import_archive(client, archive)
    assert _wait_for_gallery_import_job(client, response.json()['job_id'])['status'] == 'success'
    collections = client.get('/api/gallery/collections').json()
    assert [c['name'] for c in collections if c['is_default']] == ['Default pack (2)']


def test_archive_accepts_more_collections_than_image_limit(client):
    metadata = {'images': [], 'collections': [
        {'name': f'Pack {n}', 'asset_alias': f'pack_{n}', 'image_ids': []} for n in range(501)
    ]}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('metadata.json', json.dumps(metadata))
    response = _post_import_archive(client, archive.getvalue())
    assert response.status_code == 202, response.text
    assert _wait_for_gallery_import_job(client, response.json()['job_id'])['status'] == 'success'
    assert len(client.get('/api/gallery/collections').json()) == 501


def test_archive_501_images_and_ordered_asset_pack_roundtrip(client):
    ids = [f'bulk-{n:03}' for n in range(501)]
    metadata = {
        'images': [{'id': image_id, 'filename': f'{image_id}.png', 'prompt': image_id, 'size': 'auto'} for image_id in ids],
        'collections': [{'name': 'Bulk', 'asset_alias': 'bulk', 'image_ids': list(reversed(ids))}],
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as zf:
        zf.writestr('metadata.json', json.dumps(metadata))
        for image_id in ids:
            zf.writestr(f'images/{image_id}.png', PNG_BYTES)
    response = _post_import_archive(client, archive.getvalue())
    assert response.status_code == 202, response.text
    result = _wait_for_gallery_import_job(client, response.json()['job_id'], timeout=60)
    assert result['status'] == 'success', result
    assert result['imported_count'] == 501

    created = client.post('/api/gallery/export-jobs', json={}).json()
    result = _wait_for_gallery_export_job(client, created['job_id'], timeout=60)
    assert result['status'] == 'success', result
    saved = client.get(result['download_url']).content
    with zipfile.ZipFile(io.BytesIO(saved)) as zf:
        exported = json.loads(zf.read('metadata.json'))
    assert len(exported['images']) == 501
    assert len(exported['collections'][0]['image_ids']) == 501
    response = _post_import_archive(client, saved)
    assert response.status_code == 202, response.text
    result = _wait_for_gallery_import_job(client, response.json()['job_id'], timeout=60)
    assert result['status'] == 'success', result
    restored = next(c for c in client.get('/api/gallery/collections').json() if c['asset_alias'] == 'bulk_1')
    references = client.get(f"/api/gallery/collections/{restored['id']}/references").json()['images']
    assert [entry['prompt'] for entry in references] == list(reversed(ids))
