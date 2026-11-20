"""
Bilibili provider for Recap Studio Resource Hub.

Transparently reflects platform status:
- Official search requires enterprise / partner developer authorization.
- Direct URL bookmarking, metadata normalization, and official player embed are fully supported.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from .base import (
    BaseResourceProvider,
    ProviderConfigError,
    ResourceNotFoundError,
)
from .models import ProviderCapabilities, ResourceItem, SearchResult

_BVID_RE = re.compile(r"(BV[a-zA-Z0-9]{10})", re.IGNORECASE)
_AVID_RE = re.compile(r"(av\d+)", re.IGNORECASE)
_BILIBILI_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:bilibili\.com/video/|b23\.tv/)(BV[a-zA-Z0-9]{10}|av\d+)",
    re.IGNORECASE,
)


class BilibiliProvider(BaseResourceProvider):
    """Provider for Bilibili media references."""

    provider_id = "bilibili"
    name = "Bilibili"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            name=self.name,
            icon="📺",
            search_supported=False,
            search_configured=False,
            url_import_supported=True,
            preview_supported=True,
            local_file_supported=False,
            status_message="URL import available (Official search requires partner API credentials)",
            supported_filters=["category"],
        )

    def extract_id(self, url: str) -> Optional[str]:
        """Extract BV or AV id from a Bilibili URL or raw ID."""
        if not url:
            return None
        text = url.strip()
        match = _BILIBILI_URL_RE.search(text)
        if match:
            return match.group(1)
        match_bv = _BVID_RE.search(text)
        if match_bv:
            return match_bv.group(1)
        match_av = _AVID_RE.search(text)
        if match_av:
            return match_av.group(1)
        return None

    def normalize_url(self, url: str) -> Optional[str]:
        """Convert Bilibili URL or ID to canonical watch URL."""
        item_id = self.extract_id(url)
        if not item_id:
            return None
        return f"https://www.bilibili.com/video/{item_id}"

    def validate_reference(self, url: str) -> bool:
        """Validate if text is a Bilibili URL or recognized ID."""
        return bool(self.extract_id(url))

    def search(
        self,
        query: str,
        page_token: Optional[str] = None,
        sort_by: str = "relevance",
        limit: int = 15,
    ) -> SearchResult:
        """Search is disabled because public unauthenticated search API is not officially provided."""
        raise ProviderConfigError(
            "Bilibili official search requires enterprise API partner credentials. "
            "Please use the 'Import URL' tab to add Bilibili videos directly by link."
        )

    def get_resource_metadata(self, resource_id_or_url: str) -> Optional[ResourceItem]:
        """Create a normalized Bilibili resource item from URL or ID."""
        item_id = self.extract_id(resource_id_or_url)
        if not item_id:
            return None

        canonical = f"https://www.bilibili.com/video/{item_id}"
        now = time.time()

        # Bilibili official player iframe embed
        embed_url = f"https://player.bilibili.com/player.html?bvid={item_id}&page=1" if item_id.startswith("BV") else ""

        return ResourceItem(
            id=f"bili_{item_id}",
            provider=self.provider_id,
            resource_type="external_video",
            external_id=item_id,
            canonical_url=canonical,
            title=f"Bilibili Video ({item_id})",
            creator_name="",
            thumbnail_url=None,
            duration_seconds=None,
            category="Anime",
            created_at=now,
            updated_at=now,
            is_local=False,
            is_authorized_for_processing=False,
            extra={
                "embed_url": embed_url,
                "note": "External reference bookmark",
            },
        )
