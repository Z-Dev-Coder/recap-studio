"""
SQLite persistence layer for Recap Studio Resource Hub.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from .models import ResourceItem


class ResourceLibrary:
    """Thread-safe persistent SQLite storage for saved resources."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    external_id TEXT,
                    canonical_url TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    creator_name TEXT,
                    thumbnail_url TEXT,
                    duration_seconds REAL,
                    published_at TEXT,
                    local_file_path TEXT,
                    category TEXT DEFAULT 'Other',
                    tags TEXT DEFAULT '[]',
                    notes TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    metadata_fetched_at REAL,
                    metadata_expires_at REAL,
                    is_local INTEGER NOT NULL DEFAULT 0,
                    is_authorized_for_processing INTEGER NOT NULL DEFAULT 0,
                    attached_projects TEXT DEFAULT '[]',
                    extra TEXT DEFAULT '{}'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_res_provider ON resources (provider)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_res_category ON resources (category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_res_created ON resources (created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_res_canonical ON resources (canonical_url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_res_local ON resources (local_file_path)")
            conn.commit()

    def _row_to_item(self, row: sqlite3.Row) -> ResourceItem:
        tags = []
        try:
            tags = json.loads(row["tags"]) if row["tags"] else []
        except Exception:
            pass

        attached_projects = []
        try:
            attached_projects = json.loads(row["attached_projects"]) if row["attached_projects"] else []
        except Exception:
            pass

        extra = {}
        try:
            extra = json.loads(row["extra"]) if row["extra"] else {}
        except Exception:
            pass

        return ResourceItem(
            id=row["id"],
            provider=row["provider"],
            resource_type=row["resource_type"],
            external_id=row["external_id"],
            canonical_url=row["canonical_url"],
            title=row["title"],
            creator_name=row["creator_name"],
            thumbnail_url=row["thumbnail_url"],
            duration_seconds=row["duration_seconds"],
            published_at=row["published_at"],
            local_file_path=row["local_file_path"],
            category=row["category"] or "Other",
            tags=tags,
            notes=row["notes"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata_fetched_at=row["metadata_fetched_at"],
            metadata_expires_at=row["metadata_expires_at"],
            is_local=bool(row["is_local"]),
            is_authorized_for_processing=bool(row["is_authorized_for_processing"]),
            attached_projects=attached_projects,
            extra=extra,
        )

    def save(self, item: ResourceItem) -> ResourceItem:
        """Save a new resource or update existing if canonical_url matches (duplicate detection)."""
        now = time.time()
        with self._lock, self._connection() as conn:
            # Check for duplicate by canonical_url
            cur = conn.execute("SELECT * FROM resources WHERE canonical_url = ?", (item.canonical_url,))
            existing = cur.fetchone()

            tags_json = json.dumps(item.tags or [], ensure_ascii=False)
            extra_json = json.dumps(item.extra or {}, ensure_ascii=False)
            attached_json = json.dumps(item.attached_projects or [], ensure_ascii=False)

            if existing:
                # Merge tags and preserve user notes if incoming is empty
                existing_item = self._row_to_item(existing)
                merged_tags = list(dict.fromkeys(existing_item.tags + (item.tags or [])))
                notes = item.notes if item.notes else existing_item.notes
                category = item.category if item.category and item.category != "Other" else existing_item.category
                attached = list(dict.fromkeys(existing_item.attached_projects + (item.attached_projects or [])))

                conn.execute(
                    """
                    UPDATE resources SET
                        title = ?,
                        creator_name = COALESCE(?, creator_name),
                        thumbnail_url = COALESCE(?, thumbnail_url),
                        duration_seconds = COALESCE(?, duration_seconds),
                        category = ?,
                        tags = ?,
                        notes = ?,
                        updated_at = ?,
                        metadata_fetched_at = COALESCE(?, metadata_fetched_at),
                        metadata_expires_at = COALESCE(?, metadata_expires_at),
                        is_authorized_for_processing = MAX(is_authorized_for_processing, ?),
                        attached_projects = ?,
                        extra = ?
                    WHERE id = ?
                    """,
                    (
                        item.title or existing_item.title,
                        item.creator_name,
                        item.thumbnail_url,
                        item.duration_seconds,
                        category,
                        json.dumps(merged_tags, ensure_ascii=False),
                        notes,
                        now,
                        item.metadata_fetched_at,
                        item.metadata_expires_at,
                        1 if item.is_authorized_for_processing else 0,
                        json.dumps(attached, ensure_ascii=False),
                        extra_json,
                        existing_item.id,
                    ),
                )
                conn.commit()
                return self.get(existing_item.id) or item

            # Insert new
            conn.execute(
                """
                INSERT INTO resources (
                    id, provider, resource_type, external_id, canonical_url,
                    title, creator_name, thumbnail_url, duration_seconds, published_at,
                    local_file_path, category, tags, notes, created_at, updated_at,
                    metadata_fetched_at, metadata_expires_at, is_local,
                    is_authorized_for_processing, attached_projects, extra
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.provider,
                    item.resource_type,
                    item.external_id,
                    item.canonical_url,
                    item.title,
                    item.creator_name,
                    item.thumbnail_url,
                    item.duration_seconds,
                    item.published_at,
                    item.local_file_path,
                    item.category or "Other",
                    tags_json,
                    item.notes or "",
                    item.created_at or now,
                    now,
                    item.metadata_fetched_at,
                    item.metadata_expires_at,
                    1 if item.is_local else 0,
                    1 if item.is_authorized_for_processing else 0,
                    attached_json,
                    extra_json,
                ),
            )
            conn.commit()
            return item

    def get(self, resource_id: str) -> Optional[ResourceItem]:
        with self._lock, self._connection() as conn:
            cur = conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,))
            row = cur.fetchone()
            return self._row_to_item(row) if row else None

    def find_by_url(self, canonical_url: str) -> Optional[ResourceItem]:
        with self._lock, self._connection() as conn:
            cur = conn.execute("SELECT * FROM resources WHERE canonical_url = ?", (canonical_url,))
            row = cur.fetchone()
            return self._row_to_item(row) if row else None

    def find_by_local_path(self, local_path: str) -> Optional[ResourceItem]:
        with self._lock, self._connection() as conn:
            cur = conn.execute("SELECT * FROM resources WHERE local_file_path = ?", (local_path,))
            row = cur.fetchone()
            return self._row_to_item(row) if row else None

    def update(self, resource_id: str, patch: dict[str, Any]) -> Optional[ResourceItem]:
        allowed = {
            "title", "creator_name", "thumbnail_url", "category",
            "tags", "notes", "is_authorized_for_processing", "attached_projects"
        }
        updates = []
        vals = []
        now = time.time()

        for k, v in patch.items():
            if k not in allowed:
                continue
            if k in ("tags", "attached_projects"):
                updates.append(f"{k} = ?")
                vals.append(json.dumps(v or [], ensure_ascii=False))
            elif k == "is_authorized_for_processing":
                updates.append(f"{k} = ?")
                vals.append(1 if v else 0)
            else:
                updates.append(f"{k} = ?")
                vals.append(v)

        if not updates:
            return self.get(resource_id)

        updates.append("updated_at = ?")
        vals.append(now)
        vals.append(resource_id)

        with self._lock, self._connection() as conn:
            conn.execute(
                f"UPDATE resources SET {', '.join(updates)} WHERE id = ?",
                tuple(vals),
            )
            conn.commit()

        return self.get(resource_id)

    def delete(self, resource_id: str) -> bool:
        with self._lock, self._connection() as conn:
            cur = conn.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
            conn.commit()
            return cur.rowcount > 0

    def list_resources(
        self,
        query: Optional[str] = None,
        provider: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        sort_by: str = "newest",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ResourceItem], int]:
        clauses = []
        params = []

        if query and query.strip():
            q = f"%{query.strip()}%"
            clauses.append("(title LIKE ? OR creator_name LIKE ? OR notes LIKE ?)")
            params.extend([q, q, q])

        if provider and provider != "all":
            clauses.append("provider = ?")
            params.append(provider)

        if category and category != "all":
            clauses.append("category = ?")
            params.append(category)

        if tag and tag.strip():
            clauses.append("tags LIKE ?")
            params.append(f"%\"{tag.strip()}\"%")

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        order_sql = "ORDER BY created_at DESC"
        if sort_by == "oldest":
            order_sql = "ORDER BY created_at ASC"
        elif sort_by == "title":
            order_sql = "ORDER BY title ASC"

        with self._lock, self._connection() as conn:
            count_cur = conn.execute(f"SELECT COUNT(*) FROM resources {where_sql}", tuple(params))
            total = count_cur.fetchone()[0]

            query_sql = f"SELECT * FROM resources {where_sql} {order_sql} LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            cur = conn.execute(query_sql, tuple(params))
            items = [self._row_to_item(row) for row in cur.fetchall()]

            return items, total

    def attach_project(self, resource_id: str, project_id: str) -> bool:
        """Associate a resource with a project."""
        item = self.get(resource_id)
        if not item:
            return False
        if project_id not in item.attached_projects:
            item.attached_projects.append(project_id)
            self.update(resource_id, {"attached_projects": item.attached_projects})
        return True

    def detach_project(self, resource_id: str, project_id: str) -> bool:
        """Remove project association."""
        item = self.get(resource_id)
        if not item:
            return False
        if project_id in item.attached_projects:
            item.attached_projects.remove(project_id)
            self.update(resource_id, {"attached_projects": item.attached_projects})
        return True

    def check_and_expire_metadata(self, max_retention_days: int = 30) -> int:
        """
        Purge expired API metadata per YouTube Developer Policy.
        Preserves user-defined tags, notes, and category.
        """
        now = time.time()
        max_cutoff = now - (max_retention_days * 86400)
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE resources
                SET metadata_expires_at = NULL,
                    thumbnail_url = NULL
                WHERE provider = 'youtube'
                  AND (metadata_expires_at IS NOT NULL AND metadata_expires_at < ?)
                """,
                (now,),
            )
            count = cur.rowcount
            conn.commit()
            return count
