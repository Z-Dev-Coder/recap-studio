"""
Local media provider for Recap Studio Resource Hub.

Validates and probes local video files on disk using ffprobe and existing media utilities.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from .base import (
    BaseResourceProvider,
    ProviderConfigError,
    ResourceNotFoundError,
    ValidationError,
)
from .models import ProviderCapabilities, ResourceItem, SearchResult

SUPPORTED_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".wmv", ".ts"
}


class LocalFilesProvider(BaseResourceProvider):
    """Provider for locally stored media files."""

    provider_id = "local"
    name = "Local Media"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            name=self.name,
            icon="📁",
            search_supported=False,
            search_configured=True,
            url_import_supported=False,
            preview_supported=True,
            local_file_supported=True,
            status_message="Available (ready for local file import)",
            supported_filters=["category"],
        )

    def search(
        self,
        query: str,
        page_token: Optional[str] = None,
        sort_by: str = "relevance",
        limit: int = 15,
    ) -> SearchResult:
        raise ProviderConfigError(
            "Local file provider does not support remote text search. Use 'Browse / Add Local Video'."
        )

    def normalize_url(self, url: str) -> Optional[str]:
        if not url:
            return None
        cleaned = url.strip()
        if cleaned.startswith("file:///"):
            cleaned = cleaned[8:]
        p = Path(cleaned)
        if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file():
            return str(p.resolve())
        return None

    def validate_reference(self, url: str) -> bool:
        norm = self.normalize_url(url)
        return bool(norm and Path(norm).is_file())

    def validate_file(self, file_path_str: str) -> tuple[Path, dict]:
        """Thoroughly validate a local video file and probe its media metadata."""
        if not file_path_str:
            raise ValidationError("No file path specified.")

        path = Path(file_path_str.strip()).resolve()

        if not path.exists():
            raise ResourceNotFoundError(f"File does not exist: {path}")

        if not path.is_file():
            raise ValidationError(f"Path is not a regular file: {path}")

        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValidationError(
                f"Unsupported video format '{ext}'. Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )

        try:
            stat = path.stat()
        except OSError as exc:
            raise ValidationError(f"Cannot access file properties: {exc}") from exc

        if stat.st_size <= 0:
            raise ValidationError("File is empty (0 bytes).")

        # Test read permissions
        try:
            with open(path, "rb") as f:
                f.read(1024)
        except Exception as exc:
            raise ValidationError(f"File is not readable: {exc}") from exc

        # Probe metadata using recap media utilities if available
        meta = {
            "size_bytes": stat.st_size,
            "duration_seconds": None,
            "width": None,
            "height": None,
            "fps": None,
            "has_audio": False,
        }

        try:
            from ..media import have_ffmpeg, probe
            if have_ffmpeg():
                p_info = probe(path)
                meta["duration_seconds"] = p_info.duration
                meta["width"] = p_info.width
                meta["height"] = p_info.height
                meta["fps"] = p_info.fps
                meta["has_audio"] = p_info.has_audio
        except Exception:
            # ffprobe failure or missing tool does not crash, continues with file size
            pass

        return path, meta

    def get_resource_metadata(self, resource_id_or_url: str) -> Optional[ResourceItem]:
        try:
            path, meta = self.validate_file(resource_id_or_url)
        except Exception:
            return None

        now = time.time()
        file_id = f"local_{path.stem[:24]}_{int(now)}"
        canonical = str(path)

        res = ResourceItem(
            id=file_id,
            provider=self.provider_id,
            resource_type="local_file",
            external_id=str(path),
            canonical_url=canonical,
            title=path.stem,
            creator_name="Local User",
            thumbnail_url=None,
            duration_seconds=meta.get("duration_seconds"),
            local_file_path=str(path),
            category="Other",
            created_at=now,
            updated_at=now,
            is_local=True,
            is_authorized_for_processing=True,  # Local files selected by the user are authorized
            extra={
                "size_bytes": meta.get("size_bytes"),
                "width": meta.get("width"),
                "height": meta.get("height"),
                "fps": meta.get("fps"),
                "has_audio": meta.get("has_audio"),
            },
        )
        return res
