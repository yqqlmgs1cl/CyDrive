import sqlite3
import os
import time
from typing import List, Dict, Optional, Any

class MetaDatabase:
    """SQLite Metadata Manager for CyDrive Virtual File System."""

    def __init__(self, db_path: str = "cydrive_meta.db"):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            # Files & Directories metadata
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rel_path TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    parent_dir TEXT NOT NULL,
                    size INTEGER DEFAULT 0,
                    mtime REAL DEFAULT 0,
                    sha256 TEXT,
                    is_dir INTEGER DEFAULT 0,
                    telegram_msg_id INTEGER,
                    is_uploaded INTEGER DEFAULT 0,
                    is_cached INTEGER DEFAULT 1,
                    is_encrypted INTEGER DEFAULT 0,
                    chunk_count INTEGER DEFAULT 1,
                    mime_type TEXT,
                    created_at REAL,
                    updated_at REAL
                );
            """)

            # Large file chunks
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    telegram_msg_id INTEGER,
                    size INTEGER NOT NULL,
                    sha256 TEXT,
                    FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
                    UNIQUE(file_id, chunk_index)
                );
            """)

            # System settings and analytics
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

            # Indexing for sub-millisecond lookups
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_parent ON files(parent_dir);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_msg_id ON files(telegram_msg_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_uploaded ON files(is_uploaded);")
            conn.commit()

    def upsert_file(
        self,
        rel_path: str,
        name: str,
        parent_dir: str,
        size: int = 0,
        mtime: float = 0.0,
        sha256: Optional[str] = None,
        is_dir: bool = False,
        telegram_msg_id: Optional[int] = None,
        is_uploaded: bool = False,
        is_cached: bool = True,
        is_encrypted: bool = False,
        chunk_count: int = 1,
        mime_type: Optional[str] = None
    ) -> int:
        now = time.time()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO files (
                    rel_path, name, parent_dir, size, mtime, sha256, is_dir,
                    telegram_msg_id, is_uploaded, is_cached, is_encrypted, chunk_count, mime_type,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    name=excluded.name,
                    parent_dir=excluded.parent_dir,
                    size=excluded.size,
                    mtime=excluded.mtime,
                    sha256=coalesce(excluded.sha256, files.sha256),
                    telegram_msg_id=coalesce(excluded.telegram_msg_id, files.telegram_msg_id),
                    is_uploaded=excluded.is_uploaded,
                    is_cached=excluded.is_cached,
                    is_encrypted=excluded.is_encrypted,
                    chunk_count=excluded.chunk_count,
                    mime_type=coalesce(excluded.mime_type, files.mime_type),
                    updated_at=excluded.updated_at
            """, (
                rel_path, name, parent_dir, size, mtime, sha256, int(is_dir),
                telegram_msg_id, int(is_uploaded), int(is_cached), int(is_encrypted),
                chunk_count, mime_type, now, now
            ))
            conn.commit()
            return cursor.lastrowid

    def get_file(self, rel_path: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM files WHERE rel_path = ?", (rel_path,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_file_by_msg_id(self, msg_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM files WHERE telegram_msg_id = ?", (msg_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_dir(self, parent_dir: str = "/") -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM files WHERE parent_dir = ? ORDER BY is_dir DESC, name ASC", (parent_dir,))
            return [dict(row) for row in cursor.fetchall()]

    def list_all_files(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM files ORDER BY updated_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def search_files(self, query: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            pattern = f"%{query}%"
            cursor.execute("SELECT * FROM files WHERE name LIKE ? OR rel_path LIKE ? ORDER BY is_dir DESC, name ASC", (pattern, pattern))
            return [dict(row) for row in cursor.fetchall()]

    def upsert_chunk(
        self,
        file_id: int,
        chunk_index: int,
        telegram_msg_id: int,
        size: int,
        sha256: Optional[str] = None
    ):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO chunks (file_id, chunk_index, telegram_msg_id, size, sha256)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(file_id, chunk_index) DO UPDATE SET
                    telegram_msg_id=excluded.telegram_msg_id,
                    size=excluded.size,
                    sha256=coalesce(excluded.sha256, chunks.sha256)
            """, (file_id, chunk_index, telegram_msg_id, size, sha256))
            conn.commit()

    def get_chunks_by_file_id(self, file_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chunks WHERE file_id = ? ORDER BY chunk_index ASC", (file_id,))
            return [dict(row) for row in cursor.fetchall()]

    def delete_file(self, rel_path: str):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM files WHERE rel_path = ?", (rel_path,))
            row = cursor.fetchone()
            if row:
                file_id = row["id"]
                cursor.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
            cursor.execute("DELETE FROM files WHERE rel_path = ?", (rel_path,))
            conn.commit()

    def delete_folder_recursive(self, folder_rel_path: str) -> List[Dict[str, Any]]:
        """Delete a folder and all descendants; return deleted file records."""
        folder_rel_path = "/" + folder_rel_path.strip("/").replace("\\", "/")
        prefix = folder_rel_path.rstrip("/") + "/"

        deleted_files = []
        with self._get_conn() as conn:
            cursor = conn.cursor()
            # Collect all descendant files (non-directories) before deletion
            cursor.execute("""
                SELECT * FROM files
                WHERE rel_path = ? OR rel_path LIKE ? ESCAPE '\\'
            """, (folder_rel_path, prefix.replace("\\", "\\\\") + "%"))
            for row in cursor.fetchall():
                item = dict(row)
                if not item.get("is_dir"):
                    deleted_files.append(item)
                # Delete associated chunks
                if item.get("id"):
                    cursor.execute("DELETE FROM chunks WHERE file_id = ?", (item["id"],))

            # Delete all matching records
            cursor.execute("""
                DELETE FROM files
                WHERE rel_path = ? OR rel_path LIKE ? ESCAPE '\\'
            """, (folder_rel_path, prefix.replace("\\", "\\\\") + "%"))
            conn.commit()
        return deleted_files

    def rename_path(self, old_rel_path: str, new_rel_path: str):
        """Rename a file or folder, recursively updating children for folders."""
        old_rel_path = "/" + old_rel_path.strip("/").replace("\\", "/")
        new_rel_path = "/" + new_rel_path.strip("/").replace("\\", "/")
        old_parent = "/" + os.path.dirname(old_rel_path).strip("/").replace("\\", "/") if os.path.dirname(old_rel_path).strip("/").replace("\\", "/") else "/"
        new_parent = "/" + os.path.dirname(new_rel_path).strip("/").replace("\\", "/") if os.path.dirname(new_rel_path).strip("/").replace("\\", "/") else "/"
        new_name = os.path.basename(new_rel_path)
        now = time.time()

        with self._get_conn() as conn:
            cursor = conn.cursor()

            # Direct rename for the target itself
            cursor.execute("""
                UPDATE files
                SET rel_path = ?, parent_dir = ?, name = ?, updated_at = ?
                WHERE rel_path = ?
            """, (new_rel_path, new_parent, new_name, now, old_rel_path))

            # If it's a directory, recursively update all children paths
            if old_rel_path != "/":
                old_prefix = old_rel_path.rstrip("/") + "/"
                new_prefix = new_rel_path.rstrip("/") + "/"
                cursor.execute("""
                    SELECT id, rel_path, parent_dir, name FROM files
                    WHERE rel_path LIKE ? ESCAPE '\\'
                """, (old_prefix.replace("\\", "\\\\") + "%",))
                rows = cursor.fetchall()
                for row in rows:
                    child_old_rel = row["rel_path"]
                    child_old_parent = row["parent_dir"]
                    child_name = row["name"]
                    child_new_rel = child_old_rel.replace(child_old_rel, new_prefix + child_old_rel[len(old_prefix):], 1)
                    child_new_parent = child_old_parent.replace(child_old_parent, new_prefix + child_old_parent[len(old_prefix):], 1) if child_old_parent.startswith(old_prefix) else child_old_parent
                    cursor.execute("""
                        UPDATE files
                        SET rel_path = ?, parent_dir = ?, updated_at = ?
                        WHERE id = ?
                    """, (child_new_rel, child_new_parent, now, row["id"]))

            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total_files, SUM(size) as total_bytes FROM files WHERE is_dir = 0;")
            row = cursor.fetchone()
            total_files = row["total_files"] or 0
            total_bytes = row["total_bytes"] or 0

            cursor.execute("SELECT COUNT(*) as total_dirs FROM files WHERE is_dir = 1;")
            total_dirs = cursor.fetchone()["total_dirs"] or 0

            cursor.execute("SELECT COUNT(*) as uploaded_files FROM files WHERE is_uploaded = 1 AND is_dir = 0;")
            uploaded_files = cursor.fetchone()["uploaded_files"] or 0

            return {
                "total_files": total_files,
                "total_bytes": total_bytes,
                "total_dirs": total_dirs,
                "uploaded_files": uploaded_files,
                "pending_uploads": total_files - uploaded_files
            }
