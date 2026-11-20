import sqlite3
import json
import threading
from pathlib import Path

class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def get_conn(self):
        if not hasattr(self._local, "conn"):
            # check_same_thread=False is safe for read/write if we don't share cursors,
            # but using thread.local ensures one connection per thread anyway.
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        with self.get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    dir TEXT NOT NULL,
                    url TEXT,
                    title TEXT,
                    created REAL,
                    state JSON NOT NULL
                )
            """)
            conn.commit()

    def save_project(self, project_id: str, data: dict):
        with self.get_conn() as conn:
            conn.execute("""
                INSERT INTO projects (id, dir, url, title, created, state)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    dir=excluded.dir,
                    url=excluded.url,
                    title=excluded.title,
                    state=excluded.state
            """, (
                project_id,
                str(data.get("dir", "")),
                str(data.get("url", "")),
                str(data.get("title", "")),
                float(data.get("created", 0.0)),
                json.dumps(data)
            ))
            conn.commit()

    def load_project(self, project_id: str) -> dict | None:
        with self.get_conn() as conn:
            row = conn.execute("SELECT state FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row:
                return json.loads(row["state"])
            return None

    def get_all_projects(self) -> list[dict]:
        with self.get_conn() as conn:
            rows = conn.execute("SELECT state FROM projects ORDER BY created DESC").fetchall()
            return [json.loads(row["state"]) for row in rows]
            
    def delete_project(self, project_id: str) -> bool:
        with self.get_conn() as conn:
            cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.commit()
            return cursor.rowcount > 0
