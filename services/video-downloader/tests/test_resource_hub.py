"""
Tests for Resource Hub backend: providers, persistence, security, and project integration.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from starlette.testclient import TestClient

from ytdl.recap.project import Store
from ytdl.recap.resource_hub import (
    BilibiliProvider,
    LocalFilesProvider,
    ProviderAuthError,
    ProviderConfigError,
    ProviderNetworkError,
    ProviderQuotaError,
    ResourceHubService,
    ResourceItem,
    ResourceLibrary,
    ResourceNotFoundError,
    ValidationError,
    YouTubeProvider,
)
from ytdl.recap.resource_hub.service import is_safe_external_url
from ytdl.recap.resource_hub.youtube import mask_key, parse_iso_duration


# ==============================================================================
# 1. PROVIDER TESTS - YOUTUBE
# ==============================================================================

def test_parse_iso_duration():
    assert parse_iso_duration("PT1H2M30S") == 3750.0
    assert parse_iso_duration("PT45S") == 45.0
    assert parse_iso_duration("PT3M") == 180.0
    assert parse_iso_duration("P1DT2H") == 93600.0
    assert parse_iso_duration("") is None
    assert parse_iso_duration("invalid") is None


def test_mask_key():
    assert mask_key("") == ""
    assert mask_key("abcd") == "********"
    assert mask_key("AIzaSyD-1234567890abcdef") == "********cdef"


def test_youtube_api_key_missing():
    provider = YouTubeProvider(key_getter=lambda: "")
    caps = provider.get_capabilities()
    assert caps.search_configured is False

    with pytest.raises(ProviderConfigError, match="not configured"):
        provider.search("test query")


def test_youtube_url_normalization():
    provider = YouTubeProvider(key_getter=lambda: "dummy")
    assert provider.normalize_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert provider.normalize_url("https://youtu.be/dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert provider.normalize_url("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert provider.normalize_url("https://example.com/not-youtube") is None
    assert provider.validate_reference("https://youtu.be/dQw4w9WgXcQ") is True
    assert provider.validate_reference("https://example.com") is False


@patch("requests.get")
def test_youtube_search_success(mock_get):
    search_data = {
        "nextPageToken": "token123",
        "pageInfo": {"totalResults": 42},
        "items": [
            {
                "id": {"videoId": "vid001"},
                "snippet": {
                    "title": "Documentary Video",
                    "channelTitle": "NatGeo Channel",
                    "publishedAt": "2026-01-01T00:00:00Z",
                    "thumbnails": {"high": {"url": "https://img.youtube.com/vi/vid001/hqdefault.jpg"}},
                },
            }
        ],
    }
    video_details_data = {
        "items": [
            {
                "id": "vid001",
                "contentDetails": {"duration": "PT10M15S"},
            }
        ]
    }

    def side_effect(url, params, timeout=10):
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        if "search" in url:
            resp.json.return_value = search_data
        else:
            resp.json.return_value = video_details_data
        return resp

    mock_get.side_effect = side_effect

    provider = YouTubeProvider(key_getter=lambda: "AIzaTestKey")
    result = provider.search("nature documentary", page_token=None, sort_by="relevance")

    assert result.provider == "youtube"
    assert result.next_page_token == "token123"
    assert result.total_results == 42
    assert len(result.items) == 1
    item = result.items[0]
    assert item.external_id == "vid001"
    assert item.title == "Documentary Video"
    assert item.creator_name == "NatGeo Channel"
    assert item.duration_seconds == 615.0
    assert item.is_local is False
    assert item.is_authorized_for_processing is False


@patch("requests.get")
def test_youtube_search_empty(mock_get):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"items": [], "pageInfo": {"totalResults": 0}}
    mock_get.return_value = mock_resp

    provider = YouTubeProvider(key_getter=lambda: "AIzaTestKey")
    result = provider.search("empty query result")
    assert len(result.items) == 0
    assert result.total_results == 0


@patch("requests.get")
def test_youtube_invalid_api_key(mock_get):
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "error": {
            "errors": [{"reason": "keyInvalid"}],
            "message": "API key not valid. Please pass a valid API key.",
        }
    }
    mock_get.return_value = mock_resp

    provider = YouTubeProvider(key_getter=lambda: "bad_key")
    with pytest.raises(ProviderAuthError, match="Invalid YouTube API key"):
        provider.search("test")


@patch("requests.get")
def test_youtube_quota_exceeded(mock_get):
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 403
    mock_resp.json.return_value = {
        "error": {
            "errors": [{"reason": "quotaExceeded"}],
            "message": "The request cannot be completed because you have exceeded your quota.",
        }
    }
    mock_get.return_value = mock_resp

    provider = YouTubeProvider(key_getter=lambda: "valid_key")
    with pytest.raises(ProviderQuotaError, match="quota exceeded"):
        provider.search("test")


@patch("requests.get")
def test_youtube_network_timeout(mock_get):
    mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")

    provider = YouTubeProvider(key_getter=lambda: "valid_key")
    with pytest.raises(ProviderNetworkError, match="timed out"):
        provider.search("test")


# ==============================================================================
# 2. PROVIDER TESTS - BILIBILI
# ==============================================================================

def test_bilibili_capabilities_and_search_state():
    provider = BilibiliProvider()
    caps = provider.get_capabilities()
    assert caps.provider_id == "bilibili"
    assert caps.search_supported is False
    assert caps.url_import_supported is True
    assert "partner" in caps.status_message.lower()

    with pytest.raises(ProviderConfigError, match="partner credentials"):
        provider.search("anime")


def test_bilibili_url_import_and_normalization():
    provider = BilibiliProvider()
    assert provider.validate_reference("https://www.bilibili.com/video/BV1xx411c7mD") is True
    assert provider.validate_reference("https://b23.tv/BV1xx411c7mD") is True
    assert provider.validate_reference("BV1xx411c7mD") is True
    assert provider.validate_reference("https://youtube.com/watch?v=123") is False

    normalized = provider.normalize_url("https://b23.tv/BV1xx411c7mD")
    assert normalized == "https://www.bilibili.com/video/BV1xx411c7mD"

    item = provider.get_resource_metadata("https://www.bilibili.com/video/BV1xx411c7mD")
    assert item is not None
    assert item.provider == "bilibili"
    assert item.external_id == "BV1xx411c7mD"
    assert item.canonical_url == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert item.is_local is False
    assert item.is_authorized_for_processing is False


# ==============================================================================
# 3. RESOURCE LIBRARY TESTS
# ==============================================================================

def test_library_crud_and_persistence(tmp_path):
    db_file = tmp_path / "test_library.db"
    lib = ResourceLibrary(db_file)

    item1 = ResourceItem(
        id="item_1",
        provider="youtube",
        canonical_url="https://www.youtube.com/watch?v=vid111",
        title="Test Video 1",
        creator_name="Channel 1",
        category="Science Fiction",
        tags=["space", "future"],
        notes="Interesting concept",
        duration_seconds=300.0,
        metadata_fetched_at=time.time(),
        metadata_expires_at=time.time() + 1000,
    )
    saved = lib.save(item1)
    assert saved.id == "item_1"

    # Retrieve
    retrieved = lib.get("item_1")
    assert retrieved is not None
    assert retrieved.title == "Test Video 1"
    assert retrieved.tags == ["space", "future"]
    assert retrieved.notes == "Interesting concept"

    # Update notes and tags
    updated = lib.update("item_1", {"notes": "Updated note", "tags": ["space", "documentary"]})
    assert updated.notes == "Updated note"
    assert updated.tags == ["space", "documentary"]

    # Duplicate check on save: same canonical_url should update rather than duplicate
    item1_dup = ResourceItem(
        id="item_new_id",
        provider="youtube",
        canonical_url="https://www.youtube.com/watch?v=vid111",
        title="Test Video 1 Renamed",
        creator_name="Channel 1",
        category="Science Fiction",
        tags=["extra_tag"],
    )
    res_dup = lib.save(item1_dup)
    assert res_dup.id == "item_1"
    items, total = lib.list_resources()
    assert total == 1
    assert "extra_tag" in res_dup.tags

    # Persistence after reopening
    del lib
    lib2 = ResourceLibrary(db_file)
    reloaded = lib2.get("item_1")
    assert reloaded is not None
    assert reloaded.id == "item_1"
    assert "extra_tag" in reloaded.tags

    # Delete
    assert lib2.delete("item_1") is True
    assert lib2.get("item_1") is None
    items_after, total_after = lib2.list_resources()
    assert total_after == 0


def test_library_metadata_expiration(tmp_path):
    db_file = tmp_path / "test_exp.db"
    lib = ResourceLibrary(db_file)

    past = time.time() - 100
    item = ResourceItem(
        id="yt_exp",
        provider="youtube",
        canonical_url="https://www.youtube.com/watch?v=expired1",
        title="Expired YouTube Video",
        thumbnail_url="https://img.youtube.com/expired1.jpg",
        notes="User note survives",
        tags=["tag1"],
        metadata_expires_at=past,
    )
    lib.save(item)

    expired_count = lib.check_and_expire_metadata()
    assert expired_count == 1

    checked = lib.get("yt_exp")
    assert checked.thumbnail_url is None
    assert checked.notes == "User note survives"
    assert checked.tags == ["tag1"]


# ==============================================================================
# 4. PROJECT INTEGRATION TESTS
# ==============================================================================

def test_project_integration_local_vs_external(tmp_path):
    root_dir = tmp_path / "recap_root"
    settings_file = root_dir / "settings.json"
    store = Store(root_dir)
    service = ResourceHubService(root_dir=root_dir, settings_file=settings_file, store=store)

    # 1. Create a dummy project in store
    proj = store.create(url="", title="My Recap")

    # 2. Attach an external YouTube reference
    yt_item = ResourceItem(
        id="res_yt",
        provider="youtube",
        canonical_url="https://www.youtube.com/watch?v=external123",
        title="External Reference Video",
        is_local=False,
        is_authorized_for_processing=False,
    )
    service.library.save(yt_item)

    res_attach_ext = service.attach_to_project("res_yt", proj.id)
    assert res_attach_ext["ok"] is True
    assert res_attach_ext["is_local"] is False
    assert "authorized local video" in res_attach_ext["message"].lower()

    proj_refetched = store.get(proj.id)
    assert proj_refetched.url == "https://www.youtube.com/watch?v=external123"
    assert proj_refetched.source_file == ""  # Never mistaken for local media!

    # 3. Create a real dummy local video file
    dummy_video = tmp_path / "sample_clip.mp4"
    dummy_video.write_bytes(b"\x00\x00\x00 ftypisom" + b"\x00" * 1024)

    local_item = service.import_local_file(str(dummy_video), category="Action")
    assert local_item.is_local is True
    assert local_item.is_authorized_for_processing is True

    res_attach_local = service.attach_to_project(local_item.id, proj.id)
    assert res_attach_local["ok"] is True
    assert res_attach_local["is_local"] is True

    proj_updated = store.get(proj.id)
    assert proj_updated.source_file == str(dummy_video.resolve())


# ==============================================================================
# 5. SECURITY TESTS
# ==============================================================================

def test_security_url_validation():
    # Loopback and private IP SSRF checks
    assert is_safe_external_url("http://127.0.0.1:8000/steal") is False
    assert is_safe_external_url("http://localhost:8756/") is False
    assert is_safe_external_url("http://192.168.1.1/admin") is False
    assert is_safe_external_url("http://10.0.0.1/private") is False
    assert is_safe_external_url("http://172.16.0.1/internal") is False
    assert is_safe_external_url("file:///C:/Windows/system32") is False
    assert is_safe_external_url("javascript:alert(1)") is False

    # Valid external public URLs
    assert is_safe_external_url("https://www.youtube.com/watch?v=test") is True
    assert is_safe_external_url("https://www.bilibili.com/video/BV1test") is True
    assert is_safe_external_url("https://example.com/video.mp4") is True


def test_security_api_key_masking(tmp_path):
    root_dir = tmp_path / "sec_root"
    settings_file = root_dir / "settings.json"
    service = ResourceHubService(root_dir=root_dir, settings_file=settings_file)

    service.update_settings(youtube_api_key="AIzaSySecretApiKey1234567890")
    settings_resp = service.get_settings()

    assert settings_resp.youtube_api_key_configured is True
    # The full secret must NEVER be returned
    assert "AIzaSySecretApiKey" not in settings_resp.youtube_api_key_masked
    assert settings_resp.youtube_api_key_masked.startswith("********")
    assert settings_resp.youtube_api_key_masked.endswith("7890")


def test_security_invalid_local_file_rejection(tmp_path):
    root_dir = tmp_path / "sec_root2"
    settings_file = root_dir / "settings.json"
    service = ResourceHubService(root_dir=root_dir, settings_file=settings_file)

    # Path traversal / non-existent file
    with pytest.raises(ResourceNotFoundError):
        service.scan_local_file(str(tmp_path / "non_existent_file.mp4"))

    # Non-video extension
    txt_file = tmp_path / "malicious.exe"
    txt_file.write_bytes(b"not a video")
    with pytest.raises(ValidationError, match="Unsupported video format"):
        service.scan_local_file(str(txt_file))


# ==============================================================================
# 6. FASTAPI ENDPOINT TESTS
# ==============================================================================

def test_api_providers_endpoint():
    from ytdl.web.server import app
    client = TestClient(app)
    resp = client.get("/api/resources/providers")
    assert resp.status_code == 200
    providers = resp.json()
    p_ids = [p["provider_id"] for p in providers]
    assert "youtube" in p_ids
    assert "bilibili" in p_ids
    assert "local" in p_ids
    assert "direct" in p_ids


def test_api_settings_and_masking():
    from ytdl.web.server import app
    client = TestClient(app)

    # Save a key
    put_resp = client.put("/api/resources/settings", json={"youtube_api_key": "AIzaSyTestApiKey987654321"})
    assert put_resp.status_code == 200
    data = put_resp.json()
    assert data["youtube_api_key_configured"] is True
    assert "4321" in data["youtube_api_key_masked"]
    assert "AIzaSyTestApiKey" not in data["youtube_api_key_masked"]

    # Get settings
    get_resp = client.get("/api/resources/settings")
    assert get_resp.status_code == 200
    assert get_resp.json()["youtube_api_key_configured"] is True


def test_api_import_url_and_library_crud():
    from ytdl.web.server import app
    client = TestClient(app)

    # Import URL
    import_resp = client.post(
        "/api/resources/import-url",
        json={
            "url": "https://www.bilibili.com/video/BV1testAPI99",
            "title": "Bilibili API Test Video",
            "category": "Anime",
            "tags": ["bili", "test"],
            "notes": "Testing API",
        },
    )
    assert import_resp.status_code == 200
    item = import_resp.json()
    res_id = item["id"]
    assert item["provider"] == "bilibili"
    assert item["title"] == "Bilibili API Test Video"

    # Get from library
    get_resp = client.get(f"/api/resources/library/{res_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == res_id

    # List library
    list_resp = client.get("/api/resources/library?provider=bilibili")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1

    # Update resource
    patch_resp = client.patch(
        f"/api/resources/library/{res_id}",
        json={"notes": "Updated via PATCH", "category": "Drama"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["notes"] == "Updated via PATCH"
    assert patch_resp.json()["category"] == "Drama"

    # Delete resource
    del_resp = client.delete(f"/api/resources/library/{res_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True

    # 404 after deletion
    assert client.get(f"/api/resources/library/{res_id}").status_code == 404

