"""
Official YouTube Data API v3 provider implementation.

Complies with YouTube API Services Developer Policies:
- Only public search and metadata endpoints are used.
- No audiovisual content is downloaded through this provider.
- Cached metadata has conservative expiration (7 days, max 30 days).
"""

from __future__ import annotations

import os
import re
import time
from typing import Callable, Optional
import requests

from .base import (
    BaseResourceProvider,
    ProviderAuthError,
    ProviderConfigError,
    ProviderNetworkError,
    ProviderQuotaError,
)
from .models import ProviderCapabilities, ResourceItem, SearchResult

_YOUTUBE_ID_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})"
)

_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_iso_duration(duration_str: str) -> Optional[float]:
    """Parse an ISO 8601 duration string (e.g. 'PT4M13S') into total seconds."""
    if not duration_str:
        return None
    match = _ISO_DURATION_RE.match(duration_str)
    if not match:
        return None
    parts = match.groupdict()
    days = int(parts.get("days") or 0)
    hours = int(parts.get("hours") or 0)
    minutes = int(parts.get("minutes") or 0)
    seconds = int(parts.get("seconds") or 0)
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def mask_key(key: Optional[str]) -> str:
    """Safely mask an API key for UI display without exposing the secret."""
    if not key:
        return ""
    stripped = key.strip()
    if len(stripped) <= 4:
        return "********"
    return "********" + stripped[-4:]


class YouTubeProvider(BaseResourceProvider):
    """Provider for YouTube Data API v3."""

    provider_id = "youtube"
    name = "YouTube"

    def __init__(self, key_getter: Optional[Callable[[], Optional[str]]] = None):
        self._key_getter = key_getter

    def get_api_key(self) -> str:
        """Resolve API key from configured getter or environment variable."""
        key = None
        if self._key_getter:
            key = self._key_getter()
        if not key:
            key = os.environ.get("YOUTUBE_API_KEY", "")
        return (key or "").strip()

    def get_capabilities(self) -> ProviderCapabilities:
        api_key = self.get_api_key()
        has_key = bool(api_key)
        return ProviderCapabilities(
            provider_id=self.provider_id,
            name=self.name,
            icon="▶",
            search_supported=True,
            search_configured=has_key,
            url_import_supported=True,
            preview_supported=True,
            local_file_supported=False,
            status_message="API configured and ready" if has_key else "API key required in Settings",
            supported_filters=["relevance", "newest", "category"],
        )

    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract the 11-character video ID from a YouTube URL."""
        if not url:
            return None
        match = _YOUTUBE_ID_RE.search(url.strip())
        return match.group(1) if match else None

    def normalize_url(self, url: str) -> Optional[str]:
        """Convert any valid YouTube video URL into canonical watch URL."""
        vid = self.extract_video_id(url)
        return f"https://www.youtube.com/watch?v={vid}" if vid else None

    def validate_reference(self, url: str) -> bool:
        """Validate if the given URL corresponds to a valid YouTube video."""
        return bool(self.extract_video_id(url))

    def _handle_response_error(self, resp: requests.Response) -> None:
        """Examine error responses from YouTube API and raise domain-specific errors."""
        try:
            err_data = resp.json().get("error", {})
            errors = err_data.get("errors", [])
            reason = errors[0].get("reason", "") if errors else ""
            msg = err_data.get("message", resp.text)
        except Exception:
            reason = ""
            msg = resp.text

        status = resp.status_code
        if status == 403:
            if "quota" in reason.lower() or "quota" in msg.lower():
                raise ProviderQuotaError(
                    "YouTube API quota exceeded. Please check your Google Cloud quota or wait for daily reset."
                )
            if "keyinvalid" in reason.lower() or "forbidden" in reason.lower() or "key" in msg.lower():
                raise ProviderAuthError("Invalid YouTube API key or restricted permissions.")
            raise ProviderQuotaError(f"YouTube access forbidden: {msg}")
        elif status == 400:
            if "keyinvalid" in reason.lower() or "badrequest" in reason.lower():
                raise ProviderAuthError("Invalid YouTube API key.")
            raise ProviderConfigError(f"YouTube API request error: {msg}")
        elif status == 404:
            raise ResourceNotFoundError("YouTube video or resource not found.")
        else:
            raise ProviderNetworkError(f"YouTube API returned HTTP {status}: {msg}")

    def search(
        self,
        query: str,
        page_token: Optional[str] = None,
        sort_by: str = "relevance",
        limit: int = 15,
    ) -> SearchResult:
        """Search videos using official YouTube Data API v3 search endpoint."""
        api_key = self.get_api_key()
        if not api_key:
            raise ProviderConfigError(
                "YouTube API key is not configured. Please add your API key in Resource Hub Settings."
            )

        q = (query or "").strip()
        if not q:
            return SearchResult(items=[], next_page_token=None, total_results=0, provider=self.provider_id)

        # Order mapping
        order = "date" if sort_by in ("newest", "date") else "relevance"
        max_results = max(1, min(limit, 50))

        params = {
            "part": "snippet",
            "type": "video",
            "q": q,
            "order": order,
            "maxResults": max_results,
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token

        search_url = "https://www.googleapis.com/youtube/v3/search"
        try:
            resp = requests.get(search_url, params=params, timeout=10)
        except requests.exceptions.Timeout as exc:
            raise ProviderNetworkError("YouTube search request timed out.") from exc
        except requests.exceptions.RequestException as exc:
            raise ProviderNetworkError(f"Network error connecting to YouTube: {exc}") from exc

        if not resp.ok:
            self._handle_response_error(resp)

        data = resp.json()
        next_page = data.get("nextPageToken")
        total_results = data.get("pageInfo", {}).get("totalResults")
        items_raw = data.get("items", [])

        # Collect video IDs to fetch duration & high-res metadata via videos.list
        video_ids = [
            item.get("id", {}).get("videoId")
            for item in items_raw
            if item.get("id", {}).get("videoId")
        ]

        details_by_id = self._fetch_video_details(video_ids, api_key) if video_ids else {}

        now = time.time()
        expires = now + (7 * 86400)  # Conservative 7-day metadata cache limit

        items: list[ResourceItem] = []
        for item in items_raw:
            vid = item.get("id", {}).get("videoId")
            if not vid:
                continue
            snippet = item.get("snippet", {})
            thumbnails = snippet.get("thumbnails", {})
            thumb_url = (
                thumbnails.get("high", {}).get("url")
                or thumbnails.get("medium", {}).get("url")
                or thumbnails.get("default", {}).get("url")
            )

            detail = details_by_id.get(vid, {})
            duration_secs = detail.get("duration_seconds")

            res_item = ResourceItem(
                id=f"yt_{vid}",
                provider=self.provider_id,
                resource_type="external_video",
                external_id=vid,
                canonical_url=f"https://www.youtube.com/watch?v={vid}",
                title=snippet.get("title", ""),
                creator_name=snippet.get("channelTitle", ""),
                thumbnail_url=thumb_url,
                duration_seconds=duration_secs,
                published_at=snippet.get("publishedAt"),
                category="Other",
                created_at=now,
                updated_at=now,
                metadata_fetched_at=now,
                metadata_expires_at=expires,
                is_local=False,
                is_authorized_for_processing=False,
                extra={
                    "embed_url": f"https://www.youtube.com/embed/{vid}",
                    "channel_id": snippet.get("channelId"),
                },
            )
            items.append(res_item)

        return SearchResult(
            items=items,
            next_page_token=next_page,
            total_results=total_results,
            provider=self.provider_id,
        )

    def _fetch_video_details(self, video_ids: list[str], api_key: str) -> dict[str, dict]:
        """Fetch duration and extended details for a list of video IDs."""
        if not video_ids or not api_key:
            return {}
        ids_str = ",".join(video_ids[:50])
        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            "part": "snippet,contentDetails",
            "id": ids_str,
            "key": api_key,
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            if not resp.ok:
                return {}
            data = resp.json()
            out = {}
            for item in data.get("items", []):
                vid = item.get("id")
                content_details = item.get("contentDetails", {})
                duration_str = content_details.get("duration", "")
                duration_sec = parse_iso_duration(duration_str)
                out[vid] = {"duration_seconds": duration_sec}
            return out
        except Exception:
            return {}

    def get_resource_metadata(self, resource_id_or_url: str) -> Optional[ResourceItem]:
        """Fetch metadata for a single YouTube video ID or URL."""
        vid = self.extract_video_id(resource_id_or_url) or resource_id_or_url.strip()
        if not re.match(r"^[a-zA-Z0-9_-]{11}$", vid):
            return None

        api_key = self.get_api_key()
        if not api_key:
            # If no API key configured, generate a baseline reference object
            return ResourceItem(
                id=f"yt_{vid}",
                provider=self.provider_id,
                resource_type="external_video",
                external_id=vid,
                canonical_url=f"https://www.youtube.com/watch?v={vid}",
                title=f"YouTube Video ({vid})",
                thumbnail_url=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                is_local=False,
                is_authorized_for_processing=False,
                extra={"embed_url": f"https://www.youtube.com/embed/{vid}"},
            )

        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            "part": "snippet,contentDetails",
            "id": vid,
            "key": api_key,
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
        except requests.RequestException as exc:
            raise ProviderNetworkError(f"Network error connecting to YouTube: {exc}") from exc

        if not resp.ok:
            self._handle_response_error(resp)

        items = resp.json().get("items", [])
        if not items:
            return None

        item = items[0]
        snippet = item.get("snippet", {})
        content_details = item.get("contentDetails", {})
        duration_sec = parse_iso_duration(content_details.get("duration", ""))
        thumbnails = snippet.get("thumbnails", {})
        thumb_url = (
            thumbnails.get("high", {}).get("url")
            or thumbnails.get("medium", {}).get("url")
            or thumbnails.get("default", {}).get("url")
        )

        now = time.time()
        return ResourceItem(
            id=f"yt_{vid}",
            provider=self.provider_id,
            resource_type="external_video",
            external_id=vid,
            canonical_url=f"https://www.youtube.com/watch?v={vid}",
            title=snippet.get("title", ""),
            creator_name=snippet.get("channelTitle", ""),
            thumbnail_url=thumb_url,
            duration_seconds=duration_sec,
            published_at=snippet.get("publishedAt"),
            category="Other",
            created_at=now,
            updated_at=now,
            metadata_fetched_at=now,
            metadata_expires_at=now + (7 * 86400),
            is_local=False,
            is_authorized_for_processing=False,
            extra={
                "embed_url": f"https://www.youtube.com/embed/{vid}",
                "channel_id": snippet.get("channelId"),
            },
        )
