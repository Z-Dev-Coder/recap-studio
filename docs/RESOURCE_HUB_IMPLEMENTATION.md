# Resource Hub Implementation Plan

## Overview
This document outlines the architecture and implementation plan for the **Resource Hub** extension in **Recap Studio**. The Resource Hub enables discovery, saving, organization, previewing, and project integration of video resources from YouTube, Bilibili, direct URLs, and local video files.

---

## 1. Architectural Strategy
- **Backend Architecture (`services/video-downloader/ytdl/recap/resource_hub/`)**:
  - `models.py`: Data models (`ResourceItem`, `ProviderCapabilities`, `SearchResult`, `AttachResult`).
  - `base.py`: Abstract `BaseResourceProvider` defining capabilities, search, metadata, normalization, and URL validation.
  - `youtube.py`: Official YouTube Data API v3 integration with secure API key management, quota handling, pagination, and normalization.
  - `bilibili.py`: Bilibili provider reporting official capabilities honestly (URL bookmarking and reference management, transparently reporting official search API status without unauthorized scraping).
  - `local_files.py`: Local media provider integrating with `ffprobe` and existing media tools.
  - `library.py`: Lightweight SQLite persistence layer (`resource_library.db` in `ROOT`) with migrations, duplicate checking, filtering, search, and metadata expiration.
  - `service.py`: Orchestrator unifying providers, settings, library, and project attachments.
- **API Surface (`services/video-downloader/ytdl/web/resource_hub_api.py`)**:
  - Exposes RESTful endpoints registered onto the FastAPI application at `/api/resources/*`.
- **Frontend Integration (`services/video-downloader/ytdl/web/static/recap.html`)**:
  - First-class "Resource Hub" section in the primary navigation.
  - Header with debounced search, provider status pills, and filters.
  - Provider tabs: All Sources, YouTube, Bilibili, Local Files, Saved Library.
  - Grid of rich media cards with thumbnail, duration, channel, metadata badges, preview, open original, save to library, and add to project actions.
  - Modals for URL Import, Local File Inspector, Preview Player (YouTube embed / Bilibili player / local video), Add to Project / Create Project modal, and Resource Settings (YouTube API Key with test connection).

---

## 2. Phased Implementation Roadmap
1. **Compliance Documentation**: Write `docs/RESOURCE_HUB_COMPLIANCE.md` detailing YouTube Developer Policies compliance, no unauthorized scraping, and privacy guarantees.
2. **Backend Core Models & Provider Framework**:
   - Create `resource_hub/models.py` and `resource_hub/base.py`.
3. **YouTube Provider**:
   - Official v3 search and videos API, error handling (403 quota, 400 bad key, timeouts), key masking.
4. **Bilibili Provider**:
   - URL parsing (`BV...`, `b23.tv`), canonicalization, bookmark metadata support, capability disclosure.
5. **Local Media Provider**:
   - Verification of video formats, ffprobe integration, safe path handling.
6. **SQLite Resource Library**:
   - Persistence under `ROOT / "resource_library.db"` using standard `sqlite3`, indexes, duplicate handling, tags/notes.
7. **Service Layer & FastAPI Routes**:
   - Complete endpoints under `/api/resources/*`.
   - Integration with existing `Project` and `Store` in `recap/project.py`.
8. **Frontend UI/UX in `recap.html`**:
   - Professional dark-themed UI matching Recap Studio styles.
   - Resource Hub view and project workspace switching.
   - Live search, filtering, library management, and project attachment flow.
9. **Testing & Quality Assurance**:
   - Mocked provider tests (YouTube quota, errors, pagination).
   - Library SQLite persistence and duplicate checks.
   - Project integration tests (local source vs external reference).
   - Security tests (secret masking, SSRF / unsafe URL rejection).
   - Full regression suite execution.
