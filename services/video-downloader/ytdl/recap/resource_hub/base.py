"""
Base provider abstraction and provider errors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .models import ProviderCapabilities, ResourceItem, SearchResult


class ProviderError(Exception):
    """Base error for resource provider operations."""


class ProviderConfigError(ProviderError):
    """Raised when provider configuration or credentials are missing."""


class ProviderAuthError(ProviderError):
    """Raised when authentication fails (e.g. invalid API key)."""


class ProviderQuotaError(ProviderError):
    """Raised when provider rate limits or quotas are exceeded."""


class ProviderNetworkError(ProviderError):
    """Raised on connection timeout or network connectivity issue."""


class ResourceNotFoundError(ProviderError):
    """Raised when a requested resource is not found."""


class ValidationError(ProviderError):
    """Raised when user input or resource validation fails."""


class BaseResourceProvider(ABC):
    """Abstract base class for all resource providers."""

    provider_id: str
    name: str

    @abstractmethod
    def get_capabilities(self) -> ProviderCapabilities:
        """Return capabilities and configuration status of this provider."""

    @abstractmethod
    def search(
        self,
        query: str,
        page_token: Optional[str] = None,
        sort_by: str = "relevance",
        limit: int = 15,
    ) -> SearchResult:
        """Search videos using provider API. Raises error if unsupported or unconfigured."""

    @abstractmethod
    def normalize_url(self, url: str) -> Optional[str]:
        """Normalize URL into canonical format if belonging to this provider, else None."""

    @abstractmethod
    def validate_reference(self, url: str) -> bool:
        """Check whether the given URL is a valid reference for this provider."""

    @abstractmethod
    def get_resource_metadata(self, resource_id_or_url: str) -> Optional[ResourceItem]:
        """Retrieve resource item for the given ID or URL."""
