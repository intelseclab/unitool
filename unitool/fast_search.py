"""
unitool/fast_search.py
Cross-platform fast file search engine — no external dependencies.

Backend selection (automatic):
  macOS   → SpotlightBackend      (mdfind / Spotlight, real-time)
  Linux   → LocateBackend         (plocate / locate, real-time)
  Windows → WindowsSearchBackend  (WSearch OLE DB via PS, real-time)
  All     → IndexedBackend        (thread-pool os.scandir + SQLite, fallback)
"""
from __future__ import annotations

import os
import sys
import shutil
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class FileRecord:
    name:  str
    path:  str
    size:  int
    mtime: float   # Unix timestamp

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1].lower()


# ── macOS — Spotlight / mdfind ────────────────────────────────────────────────

class SpotlightBackend:
    """Real-time search via macOS Spotlight (mdfind). No indexing needed."""

    def is_available(self) -> bool:
        return sys.platform == 'darwin' and shutil.which('mdfind') is not None

    @property
    def is_realtime(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return 'Spotlight'

    def search(self, query: str, ext_filter: list[str] | None = None,
               size_min: int | None = None, size_max: int | None = None,
               date_from: float | None = None, date_to: float | None = None,
               max_results: int = 1000) -> tuple[list[FileRecord], int]:
        parts: list[str] = []

        if query:
            parts.append(f'kMDItemDisplayName == "*{query}*"cd')

        if ext_filter:
            ext_q = ' || '.join(
                f'kMDItemFSName == "*.{e.lstrip(".")}"cd' for e in ext_filter
            )
            parts.append(f'({ext_q})')

        if size_min is not None:
            parts.append(f'kMDItemFSSize >= {size_min}')
        if size_max is not None:
            parts.append(f'kMDItemFSSize <= {size_max}')
        if date_from is not None:
            parts.append(f'kMDItemContentChangeDate >= $time.iso({_ts_to_iso(date_from)})')
        if date_to is not None:
            parts.append(f'kMDItemContentChangeDate <= $time.iso({_ts_to_iso(date_to)})')

        mdq = ' && '.join(parts) if parts else 'kMDItemContentType != "public.folder"'

        try:
            r = subprocess.run(
                ['mdfind', mdq], capture_output=True, text=True, timeout=15)
            paths = [p for p in r.stdout.strip().splitlines() if p and os.path.isfile(p)]
            return _stat_paths(paths[:max_results]), len(paths)
        except Exception:
            return [], 0

    def scan(self, *_):
        pass  # Spotlight has its own index

    def file_count(self) -> int:
        return -1


# ── Linux — plocate / locate ─────────────────────────────────────────────────

class LocateBackend:
    """Real-time search via locate / plocate. No indexing needed."""

    def is_available(self) -> bool:
        return sys.platform.startswith('linux') and bool(
            shutil.which('plocate') or shutil.which('locate'))

    @property
    def is_realtime(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return 'plocate' if shutil.which('plocate') else 'locate'

    def search(self, query: str, ext_filter: list[str] | None = None,
               size_min: int | None = None, size_max: int | None = None,
               date_from: float | None = None, date_to: float | None = None,
               max_results: int = 1000) -> tuple[list[FileRecord], int]:
        cmd = shutil.which('plocate') or 'locate'
        args = [cmd, '-i', '--limit', str(max_results * 3)]
        if query:
            args.append(query)

        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=10)
            paths = [p for p in r.stdout.strip().splitlines() if p and os.path.isfile(p)]
        except Exception:
            return [], 0

        if ext_filter:
            exts = {e.lower() for e in ext_filter}
            paths = [p for p in paths if os.path.splitext(p)[1].lower() in exts]

        records = _stat_paths(paths[:max_results])

        if size_min is not None:
            records = [r for r in records if r.size >= size_min]
        if size_max is not None:
            records = [r for r in records if r.size <= size_max]
        if date_from is not None:
            records = [r for r in records if r.mtime >= date_from]
        if date_to is not None:
            records = [r for r in records if r.mtime <= date_to]

        return records, len(records)

    def scan(self, *_):
        pass

    def file_count(self) -> int:
        return -1


# ── Windows — Windows Search Service (WSearch) via OLE DB ────────────────────
#
# WSearch (Windows Search Service) is available on all Windows 10/11 machines
# and maintains an always-up-to-date file index — exactly like Spotlight on
# macOS or locate on Linux.  We query it through PowerShell + System.Data.OleDb
# using a PERSISTENT subprocess so the ~1s PowerShell startup is paid only once.
#
# Query syntax used:
#   LIKE 'query%'          prefix match  (fast, indexed)
#   CONTAINS(col,'"q*"')   prefix FTS    (fast, indexed)
#   LIKE '%query%'         substring     (slower, but still <100ms on WSearch)

_PS_INIT = r"""
[System.Reflection.Assembly]::LoadWithPartialName('System.Data') | Out-Null
$conn = New-Object System.Data.OleDb.OleDbConnection('Provider=Search.CollatorDSO;Extended Properties=''Application=Windows''')
$conn.Open()
while ($true) {
    $line = [Console]::ReadLine()
    if ($line -eq $null -or $line -eq 'EXIT') { break }
    try {
        $cmd = $conn.CreateCommand()
        $cmd.CommandText = $line
        $rd = $cmd.ExecuteReader()
        while ($rd.Read()) {
            $p = if ($rd.IsDBNull(0)) { "" } else { $rd.GetValue(0).ToString() }
            $s = if ($rd.FieldCount -gt 1 -and !$rd.IsDBNull(1)) { $rd.GetValue(1) } else { 0 }
            $d = if ($rd.FieldCount -gt 2 -and !$rd.IsDBNull(2)) { $rd.GetValue(2).ToFileTimeUtc() } else { 0 }
            [Console]::WriteLine("$p`t$s`t$d")
        }
        $rd.Close()
    } catch { }
    [Console]::WriteLine('---END---')
    [Console]::Out.Flush()
}
$conn.Close()
"""

_FT_EPOCH = 116_444_736_000_000_000  # 100ns between 1601 and 1970


def _ft_to_ts(ft: int) -> float:
    try:
        return (ft - _FT_EPOCH) / 10_000_000
    except Exception:
        return 0.0


class WindowsSearchBackend:
    """Real-time search via Windows Search Service (WSearch OLE DB).
    Uses a persistent PowerShell subprocess to avoid per-query startup cost."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock  = threading.Lock()
        self._ready = threading.Event()
        if sys.platform == 'win32' and self.__class__._wsearch_running():
            threading.Thread(target=self._warm_up, daemon=True).start()

    # ── Availability ──────────────────────────────────────────────────────────

    @staticmethod
    def _wsearch_running() -> bool:
        try:
            r = subprocess.run(
                ['sc', 'query', 'WSearch'],
                capture_output=True, timeout=4, creationflags=0x08000000,
            )
            return b'RUNNING' in r.stdout
        except Exception:
            return False

    def is_available(self) -> bool:
        return sys.platform == 'win32' and self.__class__._wsearch_running()

    @property
    def is_realtime(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return 'Windows Search'

    # ── Subprocess lifecycle ──────────────────────────────────────────────────

    def _warm_up(self):
        """Start the persistent PowerShell process in the background."""
        try:
            self._proc = subprocess.Popen(
                ['powershell', '-NonInteractive', '-NoProfile', '-Command', _PS_INIT],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', errors='replace',
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            # Prime it with a trivial query so the OLE DB connection is open
            self._raw_query(
                "SELECT TOP 1 System.ItemPathDisplay FROM SystemIndex "
                "WHERE System.FileName = '__unitool_warmup__'"
            )
        except Exception:
            self._proc = None
        finally:
            self._ready.set()

    def _raw_query(self, sql: str) -> list[tuple[str, int, float]]:
        """Send one SQL line, collect rows until ---END---."""
        # Bail out if the helper process never started or has already exited —
        # otherwise readline() below would spin forever on EOF.
        if not self._proc or self._proc.poll() is not None:
            return []
        try:
            self._proc.stdin.write(sql.replace('\n', ' ') + '\n')
            self._proc.stdin.flush()
            rows: list[tuple[str, int, float]] = []
            while True:
                raw = self._proc.stdout.readline()
                if raw == '':           # EOF — process closed stdout / died
                    break
                line = raw.rstrip('\n')
                if line == '---END---':
                    break
                if not line:
                    continue
                parts = line.split('\t', 2)
                path = parts[0] if parts else ''
                if not path:
                    continue
                size  = int(parts[1]) if len(parts) > 1 and parts[1] else 0
                ft    = int(parts[2]) if len(parts) > 2 and parts[2] else 0
                rows.append((path, size, _ft_to_ts(ft)))
            return rows
        except (OSError, ValueError):
            return []

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str, ext_filter: list[str] | None = None,
               size_min: int | None = None, size_max: int | None = None,
               date_from: float | None = None, date_to: float | None = None,
               scope: str | None = None,      # e.g. 'C:\\', 'D:\\Users', None = all
               max_results: int = 1000) -> tuple[list[FileRecord], int]:

        # Wait for warm-up (max 5 s)
        self._ready.wait(timeout=5.0)

        if not self._proc:
            return [], 0

        # WSearch quirks:
        # • "System.IsFolder = 0" silently returns nothing — use Python post-filter instead.
        # • CONTAINS with * unreliable via stdin; use LIKE 'q%' (prefix) instead.
        # • LIKE '%q%' (substring) not supported; only prefix 'q%' works.
        # • SCOPE: use forward slashes as URI — file:///C:/ or file:///C:/Users/john/
        where: list[str] = []

        if scope:
            uri = 'file:///' + scope.replace('\\', '/').rstrip('/') + '/'
            where.append(f"SCOPE='{uri}'")

        if query:
            safe = query.replace("'", "''")
            where.append(f"System.FileName LIKE '{safe}%'")

        if ext_filter:
            def _ext_clause(e: str) -> str:
                return f"System.FileExtension = '.{e.lstrip('.').lower()}'"
            where.append('(' + ' OR '.join(_ext_clause(e) for e in ext_filter) + ')')

        if size_min is not None:
            where.append(f'System.Size >= {size_min}')
        if size_max is not None:
            where.append(f'System.Size <= {size_max}')
        if date_from is not None:
            ft_from = int(date_from * 10_000_000) + _FT_EPOCH
            where.append(f'System.DateModified >= {ft_from}')
        if date_to is not None:
            ft_to = int(date_to * 10_000_000) + _FT_EPOCH
            where.append(f'System.DateModified <= {ft_to}')

        where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
        sql = (
            f"SELECT TOP {max_results + 1} "
            f"System.ItemPathDisplay, System.Size, System.DateModified "
            f"FROM SystemIndex {where_sql}"
        )

        with self._lock:
            raw = self._raw_query(sql)

        total = len(raw)
        records = [
            FileRecord(name=os.path.basename(p), path=p, size=s, mtime=mt)
            for p, s, mt in raw[:max_results]
            if p and os.path.isfile(p)
        ]
        return records, total

    def scan(self, *_):
        pass

    def file_count(self) -> int:
        return -1


# ── Windows + cross-platform — thread-pool os.scandir + SQLite FTS5 ──────────

_DB_DIR  = Path(os.environ.get('APPDATA', str(Path.home()))) / 'UniTool'
_DB_PATH = _DB_DIR / 'file_index.db'
_N_SCAN_WORKERS = min(32, max(8, (os.cpu_count() or 4) * 4))


class IndexedBackend:
    """Thread-pool filesystem scanner with SQLite FTS5 cache.
    Works on all platforms. Initial scan required (~15-30 s for 1M files)."""

    def __init__(self):
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        self._db = str(_DB_PATH)
        self._init_db()

    def is_available(self) -> bool:
        return True

    @property
    def is_realtime(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return 'IndexedSearch'

    # ── DB init ───────────────────────────────────────────────────────────────

    def _init_db(self):
        with sqlite3.connect(self._db) as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS files USING fts5(
                    name,
                    path     UNINDEXED,
                    size     UNINDEXED,
                    mtime    UNINDEXED,
                    ext      UNINDEXED,
                    tokenize = "unicode61 separators '._-'"
                )
            ''')
            con.commit()

    # ── Status ────────────────────────────────────────────────────────────────

    def is_indexed(self) -> bool:
        try:
            with sqlite3.connect(self._db) as con:
                n = con.execute('SELECT COUNT(*) FROM files').fetchone()[0]
                return n > 0
        except Exception:
            return False

    def file_count(self) -> int:
        try:
            with sqlite3.connect(self._db) as con:
                return con.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        except Exception:
            return 0

    # ── Scan ──────────────────────────────────────────────────────────────────

    def scan(self, roots: list[str],
             progress_cb: Callable[[int, str], None] | None = None) -> int:
        """Parallel directory scan. Returns number of files indexed."""
        records: list[tuple] = []
        lock = threading.Lock()
        count = [0]

        def scan_one(path: str) -> tuple[list[tuple], list[str]]:
            files, subdirs = [], []
            try:
                with os.scandir(path) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                subdirs.append(e.path)
                            elif e.is_file(follow_symlinks=False):
                                st   = e.stat()
                                ext  = os.path.splitext(e.name)[1].lower()
                                files.append(
                                    (e.name, e.path, st.st_size, st.st_mtime, ext))
                        except OSError:
                            pass
            except (PermissionError, OSError):
                pass
            return files, subdirs

        with ThreadPoolExecutor(max_workers=_N_SCAN_WORKERS) as ex:
            futs = {ex.submit(scan_one, r): None for r in roots}
            while futs:
                done, _ = wait(list(futs.keys()),
                               return_when=FIRST_COMPLETED, timeout=1.0)
                for f in done:
                    del futs[f]
                    try:
                        files, subdirs = f.result()
                        with lock:
                            records.extend(files)
                            count[0] += len(files)
                            if progress_cb and count[0] % 5000 < len(files):
                                last = files[-1][1] if files else ''
                                progress_cb(count[0], last)
                        for sub in subdirs:
                            futs[ex.submit(scan_one, sub)] = None
                    except Exception:
                        pass

        # Rebuild index
        with sqlite3.connect(self._db) as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('DELETE FROM files')
            con.executemany(
                'INSERT INTO files(name, path, size, mtime, ext) VALUES(?,?,?,?,?)',
                records,
            )
            con.commit()
            indexed = con.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        return indexed

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str, ext_filter: list[str] | None = None,
               size_min: int | None = None, size_max: int | None = None,
               date_from: float | None = None, date_to: float | None = None,
               max_results: int = 1000) -> tuple[list[FileRecord], int]:
        try:
            with sqlite3.connect(self._db) as con:
                con.execute('PRAGMA query_only=1')

                where_parts: list[str] = []
                params: list = []

                # FTS5 text search
                if query:
                    where_parts.append('name MATCH ?')
                    params.append(f'"{_fts_escape(query)}"*')

                # Post-FTS filters on UNINDEXED columns
                if ext_filter:
                    ph = ','.join('?' * len(ext_filter))
                    where_parts.append(f'ext IN ({ph})')
                    params.extend(ext_filter)
                if size_min is not None:
                    where_parts.append('CAST(size AS INTEGER) >= ?')
                    params.append(size_min)
                if size_max is not None:
                    where_parts.append('CAST(size AS INTEGER) <= ?')
                    params.append(size_max)
                if date_from is not None:
                    where_parts.append('CAST(mtime AS REAL) >= ?')
                    params.append(date_from)
                if date_to is not None:
                    where_parts.append('CAST(mtime AS REAL) <= ?')
                    params.append(date_to)

                where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''
                sql = (f'SELECT name, path, size, mtime FROM files '
                       f'{where} LIMIT {max_results + 1}')

                rows = con.execute(sql, params).fetchall()
                total = len(rows)
                records = [FileRecord(r[0], r[1], int(r[2] or 0), float(r[3] or 0))
                           for r in rows[:max_results]]
                return records, total
        except Exception:
            return [], 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _stat_paths(paths: list[str]) -> list[FileRecord]:
    out: list[FileRecord] = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append(FileRecord(os.path.basename(p), p, st.st_size, st.st_mtime))
        except OSError:
            pass
    return out


def _ts_to_iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _fts_escape(q: str) -> str:
    return q.replace('"', '""')


# ── Main engine ───────────────────────────────────────────────────────────────

class SearchEngine:
    """Auto-selects the best backend for the current platform."""

    def __init__(self):
        self._backend = self._pick()

    def _pick(self):
        if sys.platform == 'darwin':
            b = SpotlightBackend()
            if b.is_available():
                return b
        elif sys.platform.startswith('linux'):
            b = LocateBackend()
            if b.is_available():
                return b
        elif sys.platform == 'win32':
            # Windows Search Service — same idea as Spotlight/locate
            b = WindowsSearchBackend()
            if b.is_available():
                return b
        return IndexedBackend()

    # ── Delegation ────────────────────────────────────────────────────────────

    @property
    def is_realtime(self) -> bool:
        return self._backend.is_realtime

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def is_indexed(self) -> bool:
        if isinstance(self._backend, IndexedBackend):
            return self._backend.is_indexed()
        return True   # native backends are always ready

    def file_count(self) -> int:
        return self._backend.file_count()

    def search(self, query: str, **kwargs) -> tuple[list[FileRecord], int]:
        """Query the native backend. Scoped searches go through SearchWorker."""
        return self._backend.search(query, **kwargs)

    def scan(self, roots: list[str],
             progress_cb: Callable[[int, str], None] | None = None) -> int:
        if isinstance(self._backend, IndexedBackend):
            return self._backend.scan(roots, progress_cb)
        return 0


# ── Live path scan (incremental) ─────────────────────────────────────────────
#
# When a drive/folder scope is selected, OS indexes (WSearch/Spotlight/locate)
# may not cover it.  We scan directly using a thread pool and stream results
# into a queue so the UI can show them as they arrive.

import queue as _queue

def scan_path_incremental(
    root:        str,
    out_queue:   '_queue.Queue',   # receives (batch: list[FileRecord], total: int)
                                   # sentinel: None when done
    query:       str        = '',
    ext_filter:  list[str] | None = None,
    size_min:    int | None = None,
    size_max:    int | None = None,
    date_from:   float | None = None,
    date_to:     float | None = None,
    max_results: int        = 2000,
    stop_flag:   list | None = None,
):
    """Parallel directory scan that puts result batches into out_queue."""
    q_low     = query.lower()
    exts      = {e.lower() for e in ext_filter} if ext_filter else None
    total     = [0]
    pending   = [0]           # dirs currently being processed
    lock      = threading.Lock()
    batch:    list[FileRecord] = []
    BATCH_SZ  = 30

    def _flush(force: bool = False):
        with lock:
            if batch and (force or len(batch) >= BATCH_SZ):
                out_queue.put((list(batch), total[0]))
                batch.clear()

    def _match(entry) -> FileRecord | None:
        name = entry.name
        if q_low and q_low not in name.lower():
            return None
        ext = os.path.splitext(name)[1].lower()
        if exts and ext not in exts:
            return None
        try:
            st = entry.stat()
        except OSError:
            return None
        if size_min is not None and st.st_size < size_min:
            return None
        if size_max is not None and st.st_size > size_max:
            return None
        if date_from is not None and st.st_mtime < date_from:
            return None
        if date_to is not None and st.st_mtime > date_to:
            return None
        return FileRecord(name, entry.path, st.st_size, st.st_mtime)

    def _scan_dir(path: str) -> list[str]:
        """Scan one directory, add matching files to batch, return subdirs."""
        subdirs: list[str] = []
        if stop_flag and stop_flag[0]:
            return subdirs
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            subdirs.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            rec = _match(e)
                            if rec:
                                with lock:
                                    batch.append(rec)
                                    total[0] += 1
                                _flush()
                    except OSError:
                        pass
        except (PermissionError, OSError):
            pass
        return subdirs

    with ThreadPoolExecutor(max_workers=_N_SCAN_WORKERS) as ex:
        futs = {ex.submit(_scan_dir, root): None}
        while futs:
            if (stop_flag and stop_flag[0]) or total[0] >= max_results:
                # Signal stop so in-flight _scan_dir calls exit early
                if stop_flag is not None:
                    stop_flag[0] = True
                break
            done, _ = wait(list(futs.keys()), return_when=FIRST_COMPLETED, timeout=0.3)
            for f in done:
                del futs[f]
                try:
                    subdirs = f.result()
                    _flush(force=True)          # flush after each directory
                    if total[0] < max_results:
                        for sub in subdirs:
                            futs[ex.submit(_scan_dir, sub)] = None
                except Exception:
                    pass

    _flush(force=True)
    out_queue.put(None)   # sentinel — scan finished


# ── Drive / location helpers ──────────────────────────────────────────────────

def list_drives() -> list[str]:
    """Return mounted drive root paths, e.g. ['C:\\', 'D:\\'] on Windows."""
    if sys.platform == 'win32':
        import string
        return [f'{l}:\\' for l in string.ascii_uppercase
                if os.path.exists(f'{l}:\\')]
    elif sys.platform == 'darwin':
        try:
            return ['/'] + [f'/Volumes/{v}'
                            for v in os.listdir('/Volumes')
                            if os.path.ismount(f'/Volumes/{v}')]
        except OSError:
            return ['/']
    else:  # Linux
        import subprocess as _sp
        try:
            r = _sp.run(['lsblk','-o','MOUNTPOINT','-n','-r'],
                        capture_output=True, text=True, timeout=3)
            mounts = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith('/')]
            return sorted(set(mounts)) or ['/']
        except Exception:
            return ['/']


# ── Qt workers ────────────────────────────────────────────────────────────────

class SearchWorker(QThread):
    done    = pyqtSignal(list, int, int)   # records, total_found, elapsed_ms
    partial = pyqtSignal(list, int)        # partial_records, total_so_far

    def __init__(self, engine: SearchEngine, query: str,
                 scope: str | None = None, **kwargs):
        super().__init__()
        self._engine    = engine
        self._query     = query
        self._scope     = scope
        self._kwargs    = kwargs
        self._stop_flag = [False]

    def stop(self):
        self._stop_flag[0] = True

    def run(self):
        t0 = time.perf_counter()

        if self._scope:
            # Incremental scan — emit partial results as directories are processed
            q: _queue.Queue = _queue.Queue()
            kw = {k: v for k, v in self._kwargs.items()
                  if k in ('ext_filter','size_min','size_max','date_from','date_to')}
            scan_thread = threading.Thread(
                target=scan_path_incremental,
                args=(self._scope, q),
                kwargs=dict(query=self._query, stop_flag=self._stop_flag, **kw),
                daemon=True,
            )
            scan_thread.start()

            count = 0
            while True:
                try:
                    item = q.get(timeout=0.2)
                except _queue.Empty:
                    if not scan_thread.is_alive():
                        break
                    continue
                if item is None:
                    break
                batch, total_so_far = item
                count += len(batch)
                # Emit ONLY the new batch — the widget appends it incrementally
                # (avoids re-sending and re-rendering the whole list every time).
                self.partial.emit(batch, total_so_far)

            elapsed = int((time.perf_counter() - t0) * 1000)
            # Empty list signals "streaming finished" — rows are already on screen.
            self.done.emit([], count, elapsed)

        else:
            # Native backend (WSearch / mdfind / locate) — single fast call
            records, total = self._engine.search(self._query, **self._kwargs)
            elapsed = int((time.perf_counter() - t0) * 1000)
            self.done.emit(records, total, elapsed)


class IndexWorker(QThread):
    progress = pyqtSignal(int, str)   # files_so_far, current_path
    done     = pyqtSignal(int)        # total_indexed

    def __init__(self, engine: SearchEngine, roots: list[str]):
        super().__init__()
        self._engine = engine
        self._roots  = roots

    def run(self):
        def cb(n: int, path: str):
            self.progress.emit(n, path)
        total = self._engine.scan(self._roots, progress_cb=cb)
        self.done.emit(total)
