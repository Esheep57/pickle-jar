"""作品集 API（見 app/albums.py）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import albums

router = APIRouter()


class AlbumCreate(BaseModel):
    title: str
    ids: list[int]
    kind: str = "other"
    tags: list[dict] = []
    folder: str | None = None
    source_url: str | None = None
    note: str | None = None


class AlbumItems(BaseModel):
    ids: list[int]


class AlbumTag(BaseModel):
    name: str
    category: str | None = None
    on: bool = True


class AlbumUpdate(BaseModel):
    title: str | None = None
    kind: str | None = None
    cover: int | None = None
    note: str | None = None
    source_url: str | None = None


def _404(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except KeyError:
        raise HTTPException(404, "沒有這個本子")


@router.get("/api/albums")
def list_albums(nsfw: str = "hide"):
    return albums.listing(nsfw)


@router.post("/api/albums")
def create_album(body: AlbumCreate):
    if not body.ids:
        raise HTTPException(400, "沒有選圖")
    aid = albums.create(body.title, body.ids[:5000], body.kind, body.tags[:30], body.folder, body.source_url, body.note)
    return {"ok": True, "id": aid}


@router.get("/api/albums/suggest-folders")
def suggest_folders():
    return albums.folder_suggestions()


@router.get("/api/albums/parse")
def parse(name: str = ""):
    return {"parsed": albums.parse_name(name), "tags": albums.suggest_tags(name)}


@router.get("/api/albums/{aid}")
def get_album(aid: int):
    d = albums.detail(aid)
    if not d:
        raise HTTPException(404, "沒有這個本子")
    return d


@router.post("/api/albums/{aid}")
def update_album(aid: int, body: AlbumUpdate):
    _404(albums.update, aid, **body.model_dump())
    return {"ok": True}


@router.post("/api/albums/{aid}/add")
def add_items(aid: int, body: AlbumItems):
    return {"ok": True, "n": _404(albums.add_items, aid, body.ids[:5000])}


@router.post("/api/albums/{aid}/remove")
def remove_items(aid: int, body: AlbumItems):
    return {"ok": True, "n": _404(albums.remove_items, aid, body.ids)}


@router.post("/api/albums/{aid}/tag")
def album_tag(aid: int, body: AlbumTag):
    if not body.name.strip():
        raise HTTPException(400, "沒有標籤名稱")
    return _404(albums.set_tag, aid, body.name, body.category, body.on)


@router.post("/api/albums/{aid}/order")
def album_order(aid: int, body: AlbumItems):
    _404(albums.reorder, aid, body.ids)
    return {"ok": True}


class AlbumSort(BaseModel):
    by: str = "smart"


@router.post("/api/albums/{aid}/sort")
def album_sort_by(aid: int, body: AlbumSort):
    if body.by not in albums.SORTS:
        raise HTTPException(400, "沒有這種排序")
    return {"ok": True, "moved": _404(albums.sort_album, aid, body.by)}


@router.get("/api/albums/{aid}/sort-preview")
def album_sort_preview(aid: int, by: str = "smart"):
    if by not in albums.SORTS:
        raise HTTPException(400, "沒有這種排序")
    return _404(albums.preview, aid, by)


@router.get("/api/albums/{aid}/pages")
def album_pages(aid: int):
    return _404(albums.pages, aid)


@router.post("/api/albums/{aid}/sort-by-name")
def album_sort(aid: int):
    _404(albums.sort_by_name, aid)
    return {"ok": True}


@router.post("/api/albums/{aid}/delete")
def album_delete(aid: int, untag: bool = True):
    _404(albums.delete, aid, untag)
    return {"ok": True}
