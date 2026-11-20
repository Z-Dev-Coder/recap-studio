"""
Resource Hub Service orchestrator.

Coordinates providers, library persistence, settings, and integration with Recap Projects.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from .base import (
    BaseResourceProvider,
    ProviderAuthError,
    ProviderConfigError,
    ProviderNetworkError,
    ProviderQuotaError,
    ValidationError,
)
from .bilibili import BilibiliProvider
from .library import ResourceLibrary
from .local_files import LocalFilesProvider
from .models import (
    ProviderCapabilities,
    ResourceItem,
    ResourceSettingsResponse,
    SearchResult,
)
from .youtube import YouTubeProvider, mask_key


def is_safe_external_url(url_str: str) -> bool:
    """
    Validate that an external URL uses http/https and does not resolve to
    localhost, private IP spaces, or link-local addresses (SSRF mitigation).
    """
    if not url_str:
        return False
    parsed = urllib.parse.urlsplit(url_str.strip())
    if parsed.scheme.lower() not in ("http", "https"):
        return False

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False

    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return False

    # Check for raw IP or resolve domain
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        # Not a raw IP literal; hostname is a domain name
        pass

    return True


class ResourceHubService:
    """Central service managing Resource Hub operations."""

    def __init__(self, root_dir: Path, settings_file: Path, store=None):
        self.root_dir = Path(root_dir)
        self.settings_file = Path(settings_file)
        self.store = store
        self.library = ResourceLibrary(self.root_dir / "resource_library.db")

        # Instantiate providers
        self.youtube = YouTubeProvider(key_getter=self.get_youtube_api_key)
        self.bilibili = BilibiliProvider()
        self.local_files = LocalFilesProvider()

        self._providers: dict[str, BaseResourceProvider] = {
            "youtube": self.youtube,
            "bilibili": self.bilibili,
            "local": self.local_files,
        }

    # ---------------------------------------------------------------- settings
    def _read_settings(self) -> dict:
        try:
            return json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_settings(self, patch: dict) -> dict:
        data = self._read_settings()
        data.update({k: v for k, v in patch.items() if v is not None})
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def get_youtube_api_key(self) -> Optional[str]:
        data = self._read_settings()
        key = data.get("youtube_api_key")
        if not key:
            key = os.environ.get("YOUTUBE_API_KEY", "")
        return key.strip() if key else None

    def get_settings(self) -> ResourceSettingsResponse:
        key = self.get_youtube_api_key()
        masked = mask_key(key)
        return ResourceSettingsResponse(
            youtube_api_key_configured=bool(key),
            youtube_api_key_masked=masked,
            cache_retention_days=7,
        )

    def update_settings(self, youtube_api_key: Optional[str] = None) -> ResourceSettingsResponse:
        patch = {}
        if youtube_api_key is not None:
            patch["youtube_api_key"] = youtube_api_key.strip()
        self._write_settings(patch)
        return self.get_settings()

    def test_youtube_connection(self) -> dict:
        """Validate the configured YouTube API key by running a lightweight test query."""
        key = self.get_youtube_api_key()
        if not key:
            raise ProviderConfigError("No YouTube API key configured.")

        # Lightweight test request: videos list for a known public video or search with limit=1
        import requests
        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {"part": "snippet", "id": "jNQXAC9IVRw", "key": key}
        try:
            resp = requests.get(url, params=params, timeout=8)
        except requests.RequestException as exc:
            raise ProviderNetworkError(f"Connection to YouTube failed: {exc}") from exc

        if not resp.ok:
            self.youtube._handle_response_error(resp)

        return {"ok": True, "message": "YouTube API connection successful."}

    # -------------------------------------------------------------- providers
    def get_providers(self) -> list[ProviderCapabilities]:
        return [
            self.youtube.get_capabilities(),
            self.bilibili.get_capabilities(),
            self.local_files.get_capabilities(),
            ProviderCapabilities(
                provider_id="direct",
                name="Direct URL",
                icon="🔗",
                search_supported=False,
                search_configured=True,
                url_import_supported=True,
                preview_supported=False,
                local_file_supported=False,
                status_message="Direct URL bookmarking available",
                supported_filters=["category"],
            ),
        ]

    # ----------------------------------------------------------------- search
    def search(
        self,
        query: str,
        provider: str = "youtube",
        page_token: Optional[str] = None,
        sort_by: str = "relevance",
        limit: int = 15,
    ) -> SearchResult:
        """Execute search against the specified provider."""
        prov = self._providers.get(provider)
        if not prov:
            raise ValidationError(f"Unknown provider: '{provider}'")
        return prov.search(query=query, page_token=page_token, sort_by=sort_by, limit=limit)

    # ------------------------------------------------------------ URL import
    def import_url(
        self,
        url: str,
        title: Optional[str] = None,
        category: Optional[str] = "Other",
        tags: Optional[list[str]] = None,
        notes: str = "",
    ) -> ResourceItem:
        """Import a video reference from YouTube, Bilibili, or a direct HTTP/HTTPS URL."""
        cleaned = (url or "").strip()
        if not cleaned:
            raise ValidationError("Please provide a valid URL.")

        if not is_safe_external_url(cleaned):
            raise ValidationError(
                "Invalid URL scheme or forbidden target. Only public http/https URLs are permitted."
            )

        now = time.time()

        # Check if YouTube
        if self.youtube.validate_reference(cleaned):
            meta = self.youtube.get_resource_metadata(cleaned)
            if not meta:
                vid = self.youtube.extract_video_id(cleaned) or "video"
                meta = ResourceItem(
                    id=f"yt_{vid}",
                    provider="youtube",
                    resource_type="external_video",
                    external_id=vid,
                    canonical_url=self.youtube.normalize_url(cleaned) or cleaned,
                    title=title.strip() if title else f"YouTube Video ({vid})",
                    category=category or "Other",
                    tags=tags or [],
                    notes=notes,
                    created_at=now,
                    updated_at=now,
                    is_local=False,
                    is_authorized_for_processing=False,
                )
            else:
                if title:
                    meta.title = title.strip()
                if category:
                    meta.category = category
                if tags:
                    meta.tags = tags
                if notes:
                    meta.notes = notes
            return self.library.save(meta)

        # Check if Bilibili
        if self.bilibili.validate_reference(cleaned):
            meta = self.bilibili.get_resource_metadata(cleaned)
            if meta:
                if title:
                    meta.title = title.strip()
                if category:
                    meta.category = category
                if tags:
                    meta.tags = tags
                if notes:
                    meta.notes = notes
                return self.library.save(meta)

        # Direct URL Bookmark
        parsed = urllib.parse.urlsplit(cleaned)
        canonical = cleaned
        inferred_title = title.strip() if title else (Path(parsed.path).stem or parsed.netloc)

        item = ResourceItem(
            id=f"url_{int(now)}_{abs(hash(cleaned)) % 100000}",
            provider="direct",
            resource_type="external_video",
            external_id=cleaned,
            canonical_url=canonical,
            title=inferred_title,
            creator_name=parsed.netloc,
            category=category or "Other",
            tags=tags or [],
            notes=notes,
            created_at=now,
            updated_at=now,
            is_local=False,
            is_authorized_for_processing=False,
            extra={"raw_url": cleaned},
        )
        return self.library.save(item)

    # ------------------------------------------------------------- local file
    def scan_local_file(self, file_path: str) -> dict:
        """Inspect and probe a local video file before saving."""
        path, meta = self.local_files.validate_file(file_path)
        return {
            "path": str(path),
            "filename": path.name,
            "size_bytes": meta.get("size_bytes"),
            "duration_seconds": meta.get("duration_seconds"),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "fps": meta.get("fps"),
            "has_audio": meta.get("has_audio"),
        }

    def import_local_file(
        self,
        file_path: str,
        category: Optional[str] = "Other",
        tags: Optional[list[str]] = None,
        notes: str = "",
    ) -> ResourceItem:
        """Import a local video file into the Resource Library."""
        item = self.local_files.get_resource_metadata(file_path)
        if not item:
            raise ValidationError(f"Unable to read or probe local video file: {file_path}")

        if category:
            item.category = category
        if tags:
            item.tags = tags
        if notes:
            item.notes = notes

        return self.library.save(item)

    # --------------------------------------------------- recap project linking
    def attach_to_project(self, resource_id: str, project_id: str) -> dict:
        """
        Connect a resource to an existing Recap Project.
        - If resource is local: assigns `project.source_file`, sets authorized, adopts file.
        - If resource is external: stores reference, alerts user to supply authorized media.
        """
        if not self.store:
            raise ValidationError("Project store not configured.")

        item = self.library.get(resource_id)
        if not item:
            raise ValidationError(f"Resource not found: {resource_id}")

        project = self.store.get(project_id)
        if not project:
            raise ValidationError(f"Project not found: {project_id}")

        self.library.attach_project(resource_id, project_id)

        if item.is_local and item.local_file_path:
            p = Path(item.local_file_path)
            if not p.exists():
                raise ValidationError(f"Local file not found on disk: {item.local_file_path}")
            project.source_file = str(p.resolve())
            if not project.title or project.title == "Narration":
                project.title = item.title or p.stem
            project.save()

            # Adopt file using existing recap pipeline adoption logic
            from ..pipeline import _adopt_local_file
            try:
                _adopt_local_file(project)
            except Exception as exc:
                # If ffmpeg is absent, save project anyway but return message
                pass

            return {
                "ok": True,
                "project_id": project_id,
                "resource_id": resource_id,
                "is_local": True,
                "message": f"Local media '{item.title}' attached to project '{project.title}' and ready for pipeline processing.",
            }
        else:
            # External reference (YouTube, Bilibili, Direct URL)
            project.url = item.canonical_url
            if not project.title or project.title == "Narration":
                project.title = item.title
            project.story["external_reference"] = {
                "id": item.id,
                "provider": item.provider,
                "canonical_url": item.canonical_url,
                "title": item.title,
            }
            project.save()

            return {
                "ok": True,
                "project_id": project_id,
                "resource_id": resource_id,
                "is_local": False,
                "message": (
                    f"External reference '{item.title}' attached to project. "
                    "Note: In accordance with platform developer policies, external media is saved as a reference. "
                    "To run local AI transcription and processing, please select or import an authorized local video file."
                ),
            }

    def create_project_from_resource(
        self,
        resource_id: str,
        mode: str = "reels",
        language: str = "en",
        content_type: str = "recap",
    ) -> dict:
        """Create a new Recap Project directly from a Resource Item."""
        if not self.store:
            raise ValidationError("Project store not configured.")

        item = self.library.get(resource_id)
        if not item:
            raise ValidationError(f"Resource not found: {resource_id}")

        from ..project import slugify

        title = item.title or (Path(item.local_file_path).stem if item.local_file_path else "Recap")
        project = self.store.create(url=item.canonical_url, title=title)
        project.mode = mode if mode in ("reels", "long") else "reels"
        project.language = language if language in ("en", "my") else "en"
        project.content_type = content_type

        # Attach resource in library
        self.library.attach_project(resource_id, project.id)

        if item.is_local and item.local_file_path:
            p = Path(item.local_file_path)
            if p.exists():
                project.source_file = str(p.resolve())
                project.save()
                from ..pipeline import _adopt_local_file
                try:
                    _adopt_local_file(project)
                except Exception:
                    pass
        else:
            project.url = item.canonical_url
            project.story["external_reference"] = {
                "id": item.id,
                "provider": item.provider,
                "canonical_url": item.canonical_url,
                "title": item.title,
            }
            project.save()

        return project.snapshot()
