import os
import re
import hashlib
from pathlib import Path
from collections import defaultdict
from PyQt6.QtCore import QThread, pyqtSignal

from .translations import tr

try:
    import xxhash as _xxhash
    def _new_hasher():
        return _xxhash.xxh64()
except ImportError:
    def _new_hasher():
        return hashlib.md5()  # nosec

PARTIAL_SIZE = 65536  # 64 KB for quick pre-filter

FILE_TYPE_EXTENSIONS: dict[str, set[str] | None] = {
    'All Files': None,
    'Images': {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif',
               '.webp', '.heic', '.heif', '.raw', '.cr2', '.nef', '.arw', '.dng'},
    'Videos': {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv',
               '.webm', '.m4v', '.mpg', '.mpeg', '.ts', '.3gp'},
    'Documents': {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt',
                  '.pptx', '.txt', '.rtf', '.odt', '.ods', '.csv', '.md'},
    'Audio': {'.mp3', '.flac', '.wav', '.aac', '.ogg', '.m4a', '.wma', '.opus'},
    'Archives': {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso'},
}

COPY_PATTERNS = [
    re.compile(r'^(.+?)\s*\(\d+\)(\.[^.]+)$'),
    re.compile(r'^(.+?)\s*-\s*[Cc]opy(\.[^.]+)$'),
    re.compile(r'^(.+?)\s+[Cc]opy(\.[^.]+)$'),
    re.compile(r'^[Cc]opy\s+of\s+(.+?)(\.[^.]+)$'),
    re.compile(r'^(.+?)[-_][Cc]opy(\.[^.]+)$'),
]


def _get_base_name(filename: str) -> tuple[str, bool]:
    for pattern in COPY_PATTERNS:
        m = pattern.match(filename)
        if m:
            groups = m.groups()
            return (groups[0].strip() + groups[-1].lower(), True)
    stem, ext = os.path.splitext(filename)
    return (stem + ext.lower(), False)


def _hash_file(path: str, partial: bool = False) -> str | None:
    h = _new_hasher()
    try:
        with open(path, 'rb') as f:
            if partial:
                h.update(f.read(PARTIAL_SIZE))
            else:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


def fmt_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} PB'


class Scanner(QThread):
    progress      = pyqtSignal(int, str)
    group_found   = pyqtSignal(list)
    finished      = pyqtSignal(int, int, int)   # groups, dupes, bytes_saved
    error_occurred = pyqtSignal(str)

    def __init__(self, folders: list[str], file_type: str = 'All Files',
                 check_similarity: bool = True,
                 hash_cache: dict | None = None, parent=None):
        super().__init__(parent)
        self.folders = folders
        self.file_type = file_type
        self.check_similarity = check_similarity
        self._abort = False
        self._extensions: set[str] | None = FILE_TYPE_EXTENSIONS.get(file_type)
        self._hash_cache: dict = hash_cache or {}

    def stop(self):
        self._abort = True

    def run(self):
        try:
            self._do_scan()
        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            from .cache import save_hash_cache
            save_hash_cache(self._hash_cache)

    # ── private ──────────────────────────────────────────────────────────────

    def _collect(self) -> list[dict]:
        files: list[dict] = []
        for folder in self.folders:
            for root, dirs, names in os.walk(folder, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for name in names:
                    if self._abort:
                        return files
                    path = os.path.join(root, name)
                    if self._extensions:
                        if Path(path).suffix.lower() not in self._extensions:
                            continue
                    try:
                        st = os.stat(path)
                        if st.st_size > 0:
                            files.append({
                                'path': path,
                                'name': name,
                                'size': st.st_size,
                                'mtime': st.st_mtime,
                            })
                    except (OSError, PermissionError):
                        pass
        return files

    def _emit_group(self, items: list[dict], reason_key: str,
                    gc: int, dc: int, bs: int) -> tuple[int, int, int]:
        items = sorted(items, key=lambda x: x['mtime'])
        items[0]['keep'] = True
        for f in items[1:]:
            f['keep'] = False
        for f in items:
            f['reason_key'] = reason_key
        self.group_found.emit(list(items))
        return (gc + 1,
                dc + len(items) - 1,
                bs + items[0]['size'] * (len(items) - 1))

    def _do_scan(self):
        # Phase 1 – collect
        self.progress.emit(2, tr('scan_collecting'))
        all_files = self._collect()
        if self._abort:
            return

        total = len(all_files)
        self.progress.emit(8, tr('scan_found', n=total))

        # Phase 2 – size pre-filter
        by_size: dict[int, list] = defaultdict(list)
        for f in all_files:
            by_size[f['size']].append(f)
        size_groups = [g for g in by_size.values() if len(g) > 1]
        n_cands = sum(len(g) for g in size_groups)

        if not size_groups:
            self.progress.emit(100, tr('scan_no_dupes'))
            self.finished.emit(0, 0, 0)
            return

        self.progress.emit(12, tr('scan_partial_start', n=n_cands))

        # Phase 3 – partial hash
        done = 0
        partial_buckets: dict[tuple, list] = defaultdict(list)
        for size_group in size_groups:
            if self._abort:
                return
            sub: dict[str, list] = defaultdict(list)
            for f in size_group:
                path = f['path']
                entry = self._hash_cache.get(path)
                if (entry and entry.get('size') == f['size']
                        and entry.get('mtime') == f['mtime']
                        and 'partial' in entry):
                    ph = entry['partial']
                else:
                    ph = _hash_file(path, partial=True)
                    if ph is not None:
                        cached = self._hash_cache.setdefault(path, {})
                        cached['size'] = f['size']
                        cached['mtime'] = f['mtime']
                        cached['partial'] = ph
                if ph is not None:
                    sub[ph].append(f)
                done += 1
                self.progress.emit(
                    12 + int(done / n_cands * 30),
                    tr('scan_partial', done=done, total=n_cands))
            for ph, items in sub.items():
                if len(items) > 1:
                    partial_buckets[(items[0]['size'], ph)].extend(items)

        ph_groups = [g for g in partial_buckets.values() if len(g) > 1]
        n_ph = sum(len(g) for g in ph_groups)
        self.progress.emit(42, tr('scan_full_start', n=n_ph))

        # Phase 4 – full hash
        found_paths: set[str] = set()
        gc = dc = bs = 0
        done = 0

        for ph_group in ph_groups:
            if self._abort:
                return
            sub: dict[str, list] = defaultdict(list)
            for f in ph_group:
                path = f['path']
                entry = self._hash_cache.get(path)
                if (entry and entry.get('size') == f['size']
                        and entry.get('mtime') == f['mtime']
                        and 'full' in entry):
                    fh = entry['full']
                else:
                    fh = _hash_file(path, partial=False)
                    if fh is not None:
                        cached = self._hash_cache.setdefault(path, {})
                        cached['size'] = f['size']
                        cached['mtime'] = f['mtime']
                        cached['full'] = fh
                if fh is not None:
                    sub[fh].append(f)
                done += 1
                self.progress.emit(
                    42 + int(done / max(n_ph, 1) * 38),
                    tr('scan_full', done=done, total=n_ph))
            for fh, items in sub.items():
                if len(items) > 1:
                    for f in items:
                        found_paths.add(f['path'])
                    gc, dc, bs = self._emit_group(items, 'reason_content', gc, dc, bs)

        # Phase 5 – filename copy detection
        if self.check_similarity and not self._abort:
            self.progress.emit(80, tr('scan_sim_start'))
            by_base: dict[str, list] = defaultdict(list)
            for f in all_files:
                if f['path'] in found_paths:
                    continue
                base, is_copy = _get_base_name(f['name'])
                by_base[base].append((f, is_copy))

            done = 0
            n_bases = len(by_base)
            for base, entries in by_base.items():
                if self._abort:
                    break
                done += 1
                self.progress.emit(
                    80 + int(done / max(n_bases, 1) * 15),
                    tr('scan_sim', done=done, total=n_bases))
                if len(entries) < 2:
                    continue
                if not any(is_copy for _, is_copy in entries):
                    continue
                sub_size: dict[int, list] = defaultdict(list)
                for f, _ in entries:
                    sub_size[f['size']].append(f)
                for size_items in sub_size.values():
                    if len(size_items) > 1:
                        for f in size_items:
                            found_paths.add(f['path'])
                        gc, dc, bs = self._emit_group(size_items, 'reason_filename', gc, dc, bs)

        self.progress.emit(100, tr('scan_done', n=gc))
        self.finished.emit(gc, dc, bs)
