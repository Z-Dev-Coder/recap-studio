"""
Data models for Recap Studio Resource Hub.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional
from pydantic import BaseModel, Field


class ResourceItem(BaseModel):
    """Normalized video or media resource item."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    provider: str  # "youtube", "bilibili", "local", "direct"
    resource_type: str = "external_video"  # "external_video", "local_file"
    external_id: Optional[str] = None
    canonical_url: str
    title: str
    creator_name: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_seconds: Optional[float] = None
    published_at: Optional[str] = None
    local_file_path: Optional[str] = None
    category: Optional[str] = "Other"
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    metadata_fetched_at: Optional[float] = None
    metadata_expires_at: Optional[float] = None
    is_local: bool = False
    is_authorized_for_processing: bool = False
    attached_projects: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ProviderCapabilities(BaseModel):
    """Capabilities and configuration state for a media provider."""
    provider_id: str
    name: str
    icon: str
    search_supported: bool = False
    search_configured: bool = False
    url_import_supported: bool = True
    preview_supported: bool = True
    local_file_supported: bool = False
    status_message: str = ""
    supported_filters: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    """Response containing items and pagination details."""
    items: list[ResourceItem] = Field(default_factory=list)
    next_page_token: Optional[str] = None
    total_results: Optional[int] = None
    provider: str


class ResourceSettings(BaseModel):
    """Settings schema for Resource Hub."""
    youtube_api_key: Optional[str] = None
    cache_retention_days: int = 7


class ResourceSettingsResponse(BaseModel):
    """Public settings schema with masked secrets."""
    youtube_api_key_configured: bool = False
    youtube_api_key_masked: str = ""
    cache_retention_days: int = 7
