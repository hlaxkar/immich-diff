"""
Persistent SQLite cache for Immich asset pHashes and local media file pHashes.
"""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
import threading
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "immich-diff"
CACHE_FILE_NAME = "hashes.db"


def _parse_phash_val(val: Any) -> Optional[int]:
    """Safely parse a stored pHash value from SQLite into a 64-bit integer."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return None
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            return int(val, 16)
        except (ValueError, TypeError, OverflowError):
            return None
    try:
        return int(str(val).strip(), 16)
    except (ValueError, TypeError, OverflowError):
        return None


class HashCache:
    """Thread-safe SQLite persistent cache for visual perceptual hashes."""

    def __init__(self, cache_dir: Optional[Path] = None, enabled: bool = True):
        self.enabled = enabled
        self._lock = threading.Lock()
        self._local = threading.local()

        if self.enabled:
            target_dir = Path(cache_dir).resolve() if cache_dir else DEFAULT_CACHE_DIR
            try:
                os.makedirs(target_dir, exist_ok=True)
                self.db_path = target_dir / CACHE_FILE_NAME
                self._init_db()
            except Exception as e:
                logger.warning("Failed to initialize SQLite hash cache at %s: %s. Caching disabled.", target_dir, e)
                self.enabled = False
                self.db_path = None
        else:
            self.db_path = None

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create thread-local database connection with WAL mode."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        """Initialize tables in database and migrate schema if needed."""
        conn = self._get_connection()
        with self._lock, conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS immich_asset_hashes (
                    asset_id TEXT PRIMARY KEY,
                    phash TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS local_file_hashes (
                    path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    phash TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS immich_asset_metadata (
                    id TEXT PRIMARY KEY,
                    filename TEXT,
                    checksum TEXT,
                    file_size INTEGER,
                    datetime_original TEXT,
                    file_created_at TEXT,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_asset_updated_at ON immich_asset_metadata(updated_at);")

            # Schema migration: ensure phash columns do not have INTEGER numeric affinity
            for table_name in ("immich_asset_hashes", "local_file_hashes"):
                cur = conn.cursor()
                cur.execute(f"PRAGMA table_info({table_name});")
                columns = cur.fetchall()
                for col in columns:
                    # col[1] is name, col[2] is type
                    if col[1] == "phash" and "INT" in col[2].upper():
                        logger.debug("Migrating %s.phash column from %s to TEXT", table_name, col[2])
                        conn.execute(f"CREATE TABLE {table_name}_new AS SELECT * FROM {table_name} WHERE 0;")
                        conn.execute(f"DROP TABLE {table_name}_new;")
                        # Re-create cleanly as TEXT
                        if table_name == "immich_asset_hashes":
                            conn.execute("""
                                CREATE TABLE immich_asset_hashes_tmp (
                                    asset_id TEXT PRIMARY KEY,
                                    phash TEXT NOT NULL,
                                    updated_at TEXT NOT NULL
                                );
                            """)
                            conn.execute("""
                                INSERT OR IGNORE INTO immich_asset_hashes_tmp (asset_id, phash, updated_at)
                                SELECT asset_id, CAST(phash AS TEXT), updated_at
                                FROM immich_asset_hashes
                                WHERE typeof(phash) = 'text';
                            """)
                            conn.execute("DROP TABLE immich_asset_hashes;")
                            conn.execute("ALTER TABLE immich_asset_hashes_tmp RENAME TO immich_asset_hashes;")
                        else:
                            conn.execute("""
                                CREATE TABLE local_file_hashes_tmp (
                                    path TEXT PRIMARY KEY,
                                    mtime REAL NOT NULL,
                                    size INTEGER NOT NULL,
                                    phash TEXT NOT NULL
                                );
                            """)
                            conn.execute("""
                                INSERT OR IGNORE INTO local_file_hashes_tmp (path, mtime, size, phash)
                                SELECT path, mtime, size, CAST(phash AS TEXT)
                                FROM local_file_hashes
                                WHERE typeof(phash) = 'text';
                            """)
                            conn.execute("DROP TABLE local_file_hashes;")
                            conn.execute("ALTER TABLE local_file_hashes_tmp RENAME TO local_file_hashes;")

    def get_immich_hash(self, asset_id: str) -> Optional[int]:
        """Fetch cached pHash for an Immich asset."""
        if not self.enabled:
            return None
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT phash FROM immich_asset_hashes WHERE asset_id = ?", (asset_id,))
        row = cur.fetchone()
        if row:
            return _parse_phash_val(row[0])
        return None

    def get_immich_hashes(self, asset_ids: List[str]) -> Dict[str, int]:
        """Fetch cached pHashes for a batch of Immich assets."""
        if not self.enabled or not asset_ids:
            return {}

        results: Dict[str, int] = {}
        conn = self._get_connection()
        cur = conn.cursor()

        # Query in chunks to avoid SQLite host variable limit
        chunk_size = 500
        for i in range(0, len(asset_ids), chunk_size):
            chunk = asset_ids[i:i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            cur.execute(f"SELECT asset_id, phash FROM immich_asset_hashes WHERE asset_id IN ({placeholders})", chunk)
            for row in cur.fetchall():
                val = _parse_phash_val(row[1])
                if val is not None:
                    results[row[0]] = val

        return results

    def set_immich_hashes_batch(self, items: List[Tuple[str, int, str]]) -> None:
        """Store a batch of (asset_id, phash, updated_at) tuples."""
        if not self.enabled or not items:
            return

        db_items = [
            (aid, f"{h:016x}" if isinstance(h, int) else str(h), updated_at)
            for aid, h, updated_at in items
        ]

        conn = self._get_connection()
        with self._lock, conn:
            conn.executemany(
                "INSERT OR REPLACE INTO immich_asset_hashes (asset_id, phash, updated_at) VALUES (?, ?, ?)",
                db_items
            )

    def get_local_hash(self, path: str, mtime: float, size: int) -> Optional[int]:
        """Fetch cached pHash for a local file if mtime and size match."""
        if not self.enabled:
            return None

        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT mtime, size, phash FROM local_file_hashes WHERE path = ?", (path,))
        row = cur.fetchone()
        if row:
            cached_mtime, cached_size, phash_raw = row
            if abs(cached_mtime - mtime) < 0.001 and cached_size == size:
                return _parse_phash_val(phash_raw)
        return None

    def set_local_hash(self, path: str, mtime: float, size: int, phash: int) -> None:
        """Store pHash for a local file."""
        if not self.enabled:
            return

        phash_str = f"{phash:016x}" if isinstance(phash, int) else str(phash)
        conn = self._get_connection()
        with self._lock, conn:
            conn.execute(
                "INSERT OR REPLACE INTO local_file_hashes (path, mtime, size, phash) VALUES (?, ?, ?, ?)",
                (path, mtime, size, phash_str)
            )

    def get_immich_asset_count(self) -> int:
        """Return total count of cached Immich assets."""
        if not self.enabled:
            return 0
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM immich_asset_metadata;")
        row = cur.fetchone()
        return row[0] if row else 0

    def get_last_asset_sync_time(self) -> Optional[str]:
        """Return the latest updatedAt timestamp from cached Immich assets."""
        if not self.enabled:
            return None
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT max(updated_at) FROM immich_asset_metadata WHERE updated_at != '';")
        row = cur.fetchone()
        return row[0] if row and row[0] else None

    def get_all_immich_assets(self) -> List[Dict[str, Any]]:
        """Load all cached Immich assets from database as standard asset dicts."""
        if not self.enabled:
            return []
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, filename, checksum, file_size, datetime_original, file_created_at, updated_at FROM immich_asset_metadata;")
        rows = cur.fetchall()
        assets = []
        for aid, fname, checksum, file_size, dt_orig, created_at, updated_at in rows:
            assets.append({
                'id': aid,
                'originalFileName': fname,
                'filename': fname,
                'checksum': checksum,
                'exifInfo': {
                    'fileSizeInByte': file_size,
                    'dateTimeOriginal': dt_orig
                },
                'fileSizeInByte': file_size,
                'fileCreatedAt': created_at,
                'updatedAt': updated_at
            })
        return assets

    def upsert_immich_assets(self, assets: List[Dict[str, Any]]) -> None:
        """Batch insert or replace assets into immich_asset_metadata table."""
        if not self.enabled or not assets:
            return

        tuples = []
        for a in assets:
            aid = a.get('id') or a.get('assetId')
            if not aid:
                continue
            fname = a.get('originalFileName') or a.get('filename') or ''
            checksum = a.get('checksum') or ''
            exif = a.get('exifInfo') or {}

            # Extract file size
            file_size = 0
            if isinstance(exif, dict) and exif.get('fileSizeInByte') is not None:
                try:
                    file_size = int(exif['fileSizeInByte'])
                except (ValueError, TypeError):
                    file_size = 0
            if file_size == 0:
                for k in ('fileSizeInByte', 'size', 'originalFileSize'):
                    if a.get(k) is not None:
                        try:
                            file_size = int(a[k])
                            break
                        except (ValueError, TypeError):
                            pass

            dt_orig = exif.get('dateTimeOriginal') if isinstance(exif, dict) else None
            created_at = a.get('fileCreatedAt') or ''
            updated_at = a.get('updatedAt') or created_at or ''
            tuples.append((aid, fname, checksum, file_size, dt_orig, created_at, updated_at))

        conn = self._get_connection()
        with self._lock, conn:
            conn.executemany("""
                INSERT OR REPLACE INTO immich_asset_metadata 
                (id, filename, checksum, file_size, datetime_original, file_created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, tuples)

    def clear_immich_assets(self) -> None:
        """Clear cached Immich asset metadata."""
        if not self.enabled:
            return
        conn = self._get_connection()
        with self._lock, conn:
            conn.execute("DELETE FROM immich_asset_metadata;")

    def close(self):
        """Close connection if open."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None
