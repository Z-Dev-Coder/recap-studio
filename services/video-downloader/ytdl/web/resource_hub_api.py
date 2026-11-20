"""
FastAPI router for Resource Hub endpoints.
"""

from __future__ import annotations

from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..recap.resource_hub import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderNetworkError,
    ProviderQuotaError,
    ResourceHubService,
    ResourceItem,
    ResourceNotFoundError,
    ValidationError,
)
from .recap_api import ROOT, SETTINGS_FILE, store

router = APIRouter(prefix="/api/resources", tags=["resources"])

# Service singleton sharing the same root, settings, and project store as Recap Studio
service = ResourceHubService(root_dir=ROOT, settings_file=SETTINGS_FILE, store=store)


# ---------------------------------------------------------------- schemas
class ImportUrlRequest(BaseModel):
    url: str = Field(min_length=1)
    title: Optional[str] = None
    category: Optional[str] = "Other"
    tags: list[str] = Field(default_factory=list)
    notes: str = ""


class ScanLocalFileRequest(BaseModel):
    path: str = Field(min_length=1)


class ImportLocalFileRequest(BaseModel):
    path: str = Field(min_length=1)
    category: Optional[str] = "Other"
    tags: list[str] = Field(default_factory=list)
    notes: str = ""


class UpdateResourceRequest(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    notes: Optional[str] = None
    is_authorized_for_processing: Optional[bool] = None


class PutSettingsRequest(BaseModel):
    youtube_api_key: Optional[str] = None


class CreateProjectFromResourceRequest(BaseModel):
    resource_id: str
    mode: str = "reels"
    language: str = "en"
    content_type: str = "recap"


# ------------------------------------------------------------- providers & settings
@router.get("/providers")
def get_providers() -> list[dict]:
    return [p.model_dump() for p in service.get_providers()]


@router.get("/settings")
def get_settings() -> dict:
    return service.get_settings().model_dump()


@router.put("/settings")
@router.post("/settings")
def update_settings(req: PutSettingsRequest) -> dict:
    return service.update_settings(youtube_api_key=req.youtube_api_key).model_dump()


@router.post("/settings/test-youtube")
def test_youtube() -> dict:
    try:
        return service.test_youtube_connection()
    except ProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ProviderQuotaError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ProviderNetworkError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ----------------------------------------------------------------- search
@router.get("/search")
def search_resources(
    query: str = Query(..., min_length=1),
    provider: str = "youtube",
    page_token: Optional[str] = None,
    sort_by: str = "relevance",
    limit: int = 15,
) -> dict:
    try:
        res = service.search(query=query, provider=provider, page_token=page_token, sort_by=sort_by, limit=limit)
        return res.model_dump()
    except ProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ProviderQuotaError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ProviderNetworkError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ------------------------------------------------------------- URL import
@router.post("/import-url")
def import_url(req: ImportUrlRequest) -> dict:
    try:
        item = service.import_url(
            url=req.url,
            title=req.title,
            category=req.category,
            tags=req.tags,
            notes=req.notes,
        )
        return item.model_dump()
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ------------------------------------------------------------- local files
@router.post("/local/scan")
def scan_local_file(req: ScanLocalFileRequest) -> dict:
    try:
        return service.scan_local_file(req.path)
    except (ValidationError, ResourceNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/local/import")
def import_local_file(req: ImportLocalFileRequest) -> dict:
    try:
        item = service.import_local_file(
            file_path=req.path,
            category=req.category,
            tags=req.tags,
            notes=req.notes,
        )
        return item.model_dump()
    except (ValidationError, ResourceNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ------------------------------------------------------------- library CRUD
@router.get("/library")
def list_library(
    query: Optional[str] = None,
    provider: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    sort_by: str = "newest",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    items, total = service.library.list_resources(
        query=query,
        provider=provider,
        category=category,
        tag=tag,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [item.model_dump() for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/library")
def save_to_library(item: ResourceItem) -> dict:
    try:
        saved = service.library.save(item)
        return saved.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/library/{resource_id}")
def get_resource(resource_id: str) -> dict:
    item = service.library.get(resource_id)
    if not item:
        raise HTTPException(status_code=404, detail="Resource not found")
    return item.model_dump()


@router.patch("/library/{resource_id}")
def update_resource(resource_id: str, req: UpdateResourceRequest) -> dict:
    patch = req.model_dump(exclude_unset=True)
    item = service.library.update(resource_id, patch)
    if not item:
        raise HTTPException(status_code=404, detail="Resource not found")
    return item.model_dump()


@router.delete("/library/{resource_id}")
def delete_resource(resource_id: str) -> dict:
    ok = service.library.delete(resource_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Resource not found")
    return {"ok": True}


# ------------------------------------------------------------- project integration
@router.post("/library/{resource_id}/attach/{project_id}")
def attach_to_project(resource_id: str, project_id: str) -> dict:
    try:
        return service.attach_to_project(resource_id=resource_id, project_id=project_id)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/create-project")
def create_project_from_resource(req: CreateProjectFromResourceRequest) -> dict:
    try:
        return service.create_project_from_resource(
            resource_id=req.resource_id,
            mode=req.mode,
            language=req.language,
            content_type=req.content_type,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
