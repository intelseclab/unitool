import os
import sqlite3
from datetime import datetime, timezone
from PyQt6.QtCore import QThread, pyqtSignal
from .translations import tr
from .platform_utils import data_dir, SYSTEM_DIRS as _SYSTEM_DIRS

_DATA_DIR = data_dir('UniTool')
DB_PATH   = os.path.join(_DATA_DIR, 'file_index.db')

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

CREATE TABLE IF NOT EXISTS files (
    id     INTEGER PRIMARY KEY,
    name   TEXT    NOT NULL,
    path   TEXT    NOT NULL UNIQUE,
    ext    TEXT,
    size   INTEGER,
    mtime  REAL,
    folder TEXT
);
CREATE INDEX IF NOT EXISTS idx_name   ON files (name   COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_ext    ON files (ext);
CREATE INDEX IF NOT EXISTS idx_size   ON files (size);
CREATE INDEX IF NOT EXISTS idx_mtime  ON files (mtime);
CREATE INDEX IF NOT EXISTS idx_folder ON files (folder);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_db(path: str = DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()


def get_stats(path: str = DB_PATH) -> dict:
    try:
        conn = sqlite3.connect(path)
        count   = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        folders = conn.execute("SELECT COUNT(DISTINCT folder) FROM files").fetchone()[0]
        row     = conn.execute(
            "SELECT value FROM meta WHERE key='last_indexed'").fetchone()
        conn.close()
        return {'count': count, 'folders': folders,
                'last_indexed': row[0] if row else None}
    except Exception:
        return {'count': 0, 'folders': 0, 'last_indexed': None}


def search_files(query: str = '',
                 ext_filter: set | None = None,
                 size_min: int | None = None,
                 size_max: int | None = None,
                 date_cutoff: float | None = None,
                 limit: int = 2000,
                 path: str = DB_PATH) -> list[tuple]:
    try:
        conn = sqlite3.connect(path)
        parts: list[str] = []
        params: list     = []

        if query:
            parts.append("name LIKE ?")
            params.append(f'%{query}%')
        if ext_filter:
            ph = ','.join('?' * len(ext_filter))
            parts.append(f"ext IN ({ph})")
            params.extend(sorted(ext_filter))
        if size_min is not None:
            parts.append("size >= ?");  params.append(size_min)
        if size_max is not None:
            parts.append("size <= ?");  params.append(size_max)
        if date_cutoff is not None:
            parts.append("mtime >= ?"); params.append(date_cutoff)

        where = ("WHERE " + " AND ".join(parts)) if parts else ""
        sql   = (f"SELECT name, path, size, mtime FROM files "
                 f"{where} ORDER BY mtime DESC LIMIT ?")
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


class Indexer(QThread):
    progress       = pyqtSignal(int, str)
    finished       = pyqtSignal(int, int)   # total_files, folder_count
    error_occurred = pyqtSignal(str)

    def __init__(self, folders: list[str],
                 exclude_hidden: bool = True,
                 exclude_system: bool = True,
                 min_size: int = 0,
                 db_path: str = DB_PATH,
                 parent=None):
        super().__init__(parent)
        self.folders        = [os.path.normpath(f) for f in folders]
        self.db_path        = db_path
        self.exclude_hidden = exclude_hidden
        self.exclude_system = exclude_system
        self.min_size       = min_size
        self._abort         = False

    def stop(self):
        self._abort = True

    def run(self):
        try:
            self._do_index()
        except Exception as exc:
            self.error_occurred.emit(str(exc))

    # ── private ──────────────────────────────────────────────────────────────

    def _scan_dir(self, path: str):
        """Yield (name, norm_path, ext, size, mtime) using scandir — one syscall per entry."""
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if self._abort:
                        return
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if self.exclude_hidden and entry.name.startswith('.'):
                                continue
                            if self.exclude_system and entry.name.lower() in _SYSTEM_DIRS:
                                continue
                            yield from self._scan_dir(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            if self.exclude_hidden and entry.name.startswith('.'):
                                continue
                            st   = entry.stat(follow_symlinks=False)
                            if st.st_size < self.min_size:
                                continue
                            norm = os.path.normpath(entry.path)
                            ext  = os.path.splitext(entry.name)[1].lower()
                            yield (entry.name, norm, ext, st.st_size, st.st_mtime)
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            pass

    def _do_index(self):
        init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-65536")  # 64 MB page cache
        conn.execute("PRAGMA temp_store=MEMORY")

        n_folders   = max(len(self.folders), 1)
        grand_total = 0

        for fi, folder in enumerate(self.folders):
            if self._abort:
                break

            base_pct = int(fi / n_folders * 100)
            self.progress.emit(base_pct,
                tr('srch_indexing_progress',
                   folder=os.path.basename(folder) or folder,
                   count=grand_total))

            existing: dict[str, float] = {}
            try:
                for row in conn.execute(
                        "SELECT path, mtime FROM files WHERE folder = ?", (folder,)):
                    existing[row[0]] = row[1]
            except Exception:
                pass

            seen:      set[str]    = set()
            to_insert: list[tuple] = []

            for name, path, ext, size, mtime in self._scan_dir(folder):
                if self._abort:
                    break
                seen.add(path)
                grand_total += 1

                if path not in existing:
                    to_insert.append((name, path, ext, size, mtime, folder))
                elif existing[path] != mtime:
                    conn.execute(
                        "UPDATE files SET name=?,ext=?,size=?,mtime=? WHERE path=?",
                        (name, ext, size, mtime, path))

                if len(to_insert) >= 1000:
                    conn.executemany(
                        "INSERT OR IGNORE INTO files "
                        "(name,path,ext,size,mtime,folder) VALUES (?,?,?,?,?,?)",
                        to_insert)
                    to_insert.clear()
                    conn.commit()
                    pct = base_pct + int(1 / n_folders * 90)
                    self.progress.emit(min(pct, 99),
                        tr('srch_indexing_progress',
                           folder=os.path.basename(folder) or folder,
                           count=grand_total))

            if to_insert:
                conn.executemany(
                    "INSERT OR IGNORE INTO files "
                    "(name,path,ext,size,mtime,folder) VALUES (?,?,?,?,?,?)",
                    to_insert)

            removed = set(existing.keys()) - seen
            if removed:
                conn.executemany(
                    "DELETE FROM files WHERE path=?", [(p,) for p in removed])

            conn.commit()

        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('last_indexed',?)", (ts,))
        conn.commit()
        conn.close()

        stats = get_stats(self.db_path)
        self.progress.emit(100,
            tr('srch_index_done', count=stats['count'], folders=stats['folders']))
        self.finished.emit(stats['count'], stats['folders'])
