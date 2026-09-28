"""Named gallery collections (albums).

Included before the gallery query/batch routers so `/api/gallery/collections`
is not captured by `/api/gallery/{image_id}`.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Response

from ...repositories.gallery.collections import (
    GalleryCollectionNameConflictError,
    add_gallery_collection_items,
    add_gallery_collection_items_by_filters,
    create_gallery_collection,
    delete_gallery_collection,
    get_gallery_collection,
    get_gallery_image_collection_ids,
    list_gallery_collections,
    remove_gallery_collection_items,
    remove_gallery_collection_items_by_filters,
    reorder_gallery_collections,
    update_gallery_collection,
)
from ...repositories.gallery.queries import get_gallery_entry
from ...schemas.gallery import (
    GalleryBatchRequest,
    GalleryCollection,
    GalleryCollectionCreateRequest,
    GalleryCollectionItemsResponse,
    GalleryCollectionOrderRequest,
    GalleryCollectionUpdateRequest,
    GalleryImageCollectionsResponse,
)
from ...services.gallery_common import _gallery_filters_from_selection_token

router = APIRouter()


async def _require_collection(collection_id: str) -> dict:
    collection = await asyncio.to_thread(get_gallery_collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


@router.get("/api/gallery/collections", response_model=list[GalleryCollection])
async def list_collections():
    return await asyncio.to_thread(list_gallery_collections)


@router.post("/api/gallery/collections", response_model=GalleryCollection, status_code=201)
async def create_collection(req: GalleryCollectionCreateRequest):
    try:
        return await asyncio.to_thread(create_gallery_collection, req.name)
    except GalleryCollectionNameConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.put("/api/gallery/collections/order", response_model=list[GalleryCollection])
async def reorder_collections(req: GalleryCollectionOrderRequest):
    collections = await asyncio.to_thread(reorder_gallery_collections, req.ids)
    if collections is None:
        raise HTTPException(status_code=422, detail="ids must list every collection exactly once")
    return collections


@router.patch("/api/gallery/collections/{collection_id}", response_model=GalleryCollection)
async def update_collection(collection_id: str, req: GalleryCollectionUpdateRequest):
    try:
        collection = await asyncio.to_thread(
            update_gallery_collection,
            collection_id,
            name=req.name,
            is_default=req.is_default,
        )
    except GalleryCollectionNameConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


@router.delete("/api/gallery/collections/{collection_id}", status_code=204)
async def delete_collection(collection_id: str):
    deleted = await asyncio.to_thread(delete_gallery_collection, collection_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Collection not found")
    return Response(status_code=204)


async def _change_collection_items(collection_id: str, req: GalleryBatchRequest, *, add: bool) -> dict:
    await _require_collection(collection_id)
    if req.selection_token:
        filters = await _gallery_filters_from_selection_token(req.selection_token)
        operation = add_gallery_collection_items_by_filters if add else remove_gallery_collection_items_by_filters
        changed = await asyncio.to_thread(operation, collection_id, filters)
    else:
        operation = add_gallery_collection_items if add else remove_gallery_collection_items
        changed = await asyncio.to_thread(operation, collection_id, req.ids or [])
    return {
        "collection": await _require_collection(collection_id),
        "changed_count": changed,
    }


@router.post("/api/gallery/collections/{collection_id}/items", response_model=GalleryCollectionItemsResponse)
async def add_collection_items(collection_id: str, req: GalleryBatchRequest):
    return await _change_collection_items(collection_id, req, add=True)


@router.post("/api/gallery/collections/{collection_id}/items/remove", response_model=GalleryCollectionItemsResponse)
async def remove_collection_items(collection_id: str, req: GalleryBatchRequest):
    return await _change_collection_items(collection_id, req, add=False)


@router.get("/api/gallery/{image_id}/collections", response_model=GalleryImageCollectionsResponse)
async def get_image_collections(image_id: str):
    entry = await asyncio.to_thread(get_gallery_entry, image_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Image not found")
    collection_ids = await asyncio.to_thread(get_gallery_image_collection_ids, image_id)
    return {"image_id": image_id, "collection_ids": collection_ids}
