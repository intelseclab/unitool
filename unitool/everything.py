"""
unitool/everything.py
Everything SDK backend — ctypes DLL interface for instant file search.
Requires Everything (voidtools) to be installed and running.
Windows only.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

# ── DLL location ──────────────────────────────────────────────────────────────

def _find_dll() -> str | None:
    name = 'Everything64.dll' if sys.maxsize > 2**32 else 'Everything32.dll'
    candidates = [
        # SDK folder (development / bundled)
        Path(__file__).parent.parent / 'Everything-SDK' / 'dll' / name,
        # Standard Everything install paths
        Path('C:/Program Files/Everything') / name,
        Path('C:/Program Files (x86)/Everything') / name,
        # Same directory as the executable
        Path(sys.executable).parent / name,
        Path.cwd() / name,
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


# ── FILETIME → Unix timestamp ─────────────────────────────────────────────────
# FILETIME = 100-nanosecond intervals since 1601-01-01 UTC

_FT_EPOCH_DIFF = 116_444_736_000_000_000  # 100ns ticks between 1601 and 1970


def _ft_to_ts(ft: int) -> float | None:
    if ft <= 0:
        return None
    try:
        return (ft - _FT_EPOCH_DIFF) / 10_000_000
    except Exception:
        return None


# ── Request flags ─────────────────────────────────────────────────────────────

_REQ_FILENAME      = 0x00000001
_REQ_PATH          = 0x00000002
_REQ_SIZE          = 0x00000010
_REQ_DATE_MODIFIED = 0x00000040
_DEFAULT_FLAGS     = _REQ_FILENAME | _REQ_PATH | _REQ_SIZE | _REQ_DATE_MODIFIED

# ── Sort constants ─────────────────────────────────────────────────────────────

SORT_NAME_ASC      = 1
SORT_NAME_DESC     = 2
SORT_PATH_ASC      = 3
SORT_PATH_DESC     = 4
SORT_SIZE_ASC      = 5
SORT_SIZE_DESC     = 6
SORT_DATE_MOD_ASC  = 13
SORT_DATE_MOD_DESC = 14

# ── Error codes ───────────────────────────────────────────────────────────────

_ERR_IPC = 2   # Everything service not running


# ── Main class ────────────────────────────────────────────────────────────────

class EverythingSearch:
    """Thin wrapper around the Everything DLL.  One instance per process."""

    def __init__(self):
        self._dll  = None
        self._path = _find_dll()
        if self._path:
            try:
                self._dll = ctypes.CDLL(self._path)
                self._wire()
            except OSError:
                self._dll = None

    # ── Type wiring ───────────────────────────────────────────────────────────

    def _wire(self):
        d = self._dll

        # Search parameters
        d.Everything_SetSearchW.argtypes    = [wintypes.LPCWSTR]
        d.Everything_SetSearchW.restype     = None
        d.Everything_SetMatchCase.argtypes  = [wintypes.BOOL]
        d.Everything_SetMatchCase.restype   = None
        d.Everything_SetRegex.argtypes      = [wintypes.BOOL]
        d.Everything_SetRegex.restype       = None
        d.Everything_SetMax.argtypes        = [wintypes.DWORD]
        d.Everything_SetMax.restype         = None
        d.Everything_SetOffset.argtypes     = [wintypes.DWORD]
        d.Everything_SetOffset.restype      = None
        d.Everything_SetSort.argtypes       = [wintypes.DWORD]
        d.Everything_SetSort.restype        = None
        d.Everything_SetRequestFlags.argtypes = [wintypes.DWORD]
        d.Everything_SetRequestFlags.restype  = None

        # Execute
        d.Everything_QueryW.argtypes = [wintypes.BOOL]
        d.Everything_QueryW.restype  = wintypes.BOOL

        # Result counts
        d.Everything_GetNumResults.restype  = wintypes.DWORD
        d.Everything_GetTotResults.restype  = wintypes.DWORD
        d.Everything_GetLastError.restype   = wintypes.DWORD

        # Result accessors — name / path
        d.Everything_GetResultFileNameW.argtypes = [wintypes.DWORD]
        d.Everything_GetResultFileNameW.restype  = wintypes.LPCWSTR
        d.Everything_GetResultPathW.argtypes     = [wintypes.DWORD]
        d.Everything_GetResultPathW.restype      = wintypes.LPCWSTR

        # Result accessors — size / date (LARGE_INTEGER as c_int64)
        d.Everything_GetResultSize.argtypes         = [wintypes.DWORD, ctypes.POINTER(ctypes.c_int64)]
        d.Everything_GetResultSize.restype          = wintypes.BOOL
        d.Everything_GetResultDateModified.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_int64)]
        d.Everything_GetResultDateModified.restype  = wintypes.BOOL

        # Type check
        d.Everything_IsFolderResult.argtypes = [wintypes.DWORD]
        d.Everything_IsFolderResult.restype  = wintypes.BOOL

        # State
        d.Everything_Reset.restype    = None
        d.Everything_CleanUp.restype  = None
        d.Everything_IsDBLoaded.restype       = wintypes.BOOL
        d.Everything_GetMajorVersion.restype  = wintypes.DWORD
        d.Everything_GetMinorVersion.restype  = wintypes.DWORD
        d.Everything_GetRevision.restype      = wintypes.DWORD

    # ── Status ────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """True if the DLL loaded and Everything's database is ready."""
        if not self._dll:
            return False
        try:
            return bool(self._dll.Everything_IsDBLoaded())
        except Exception:
            return False

    def version(self) -> str:
        if not self._dll:
            return ''
        try:
            return (f'{self._dll.Everything_GetMajorVersion()}.'
                    f'{self._dll.Everything_GetMinorVersion()}.'
                    f'{self._dll.Everything_GetRevision()}')
        except Exception:
            return ''

    def dll_found(self) -> bool:
        return self._dll is not None

    # ── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query:       str,
        max_results: int = 1000,
        sort:        int = SORT_NAME_ASC,
    ) -> tuple[list[tuple], int, int]:
        """
        Synchronous search.  Returns (rows, total_found, elapsed_ms).

        Each row: (name: str, full_path: str, size_bytes: int, mtime: float|None)
        total_found may exceed len(rows) if max_results was hit.
        """
        if not self._dll:
            return [], 0, 0

        t0 = time.perf_counter()
        d  = self._dll

        d.Everything_Reset()
        d.Everything_SetSearchW(query or '*')
        d.Everything_SetRequestFlags(_DEFAULT_FLAGS)
        d.Everything_SetMax(max_results)
        d.Everything_SetSort(sort)

        if not d.Everything_QueryW(True):
            return [], 0, 0

        n_batch = d.Everything_GetNumResults()
        n_total = d.Everything_GetTotResults()

        size_buf = ctypes.c_int64(0)
        date_buf = ctypes.c_int64(0)
        rows: list[tuple] = []

        for i in range(n_batch):
            if d.Everything_IsFolderResult(i):
                continue

            name = d.Everything_GetResultFileNameW(i) or ''
            path = d.Everything_GetResultPathW(i) or ''
            full = f'{path}\\{name}' if path else name

            size = 0
            if d.Everything_GetResultSize(i, ctypes.byref(size_buf)):
                size = max(0, size_buf.value)

            mtime: float | None = None
            if d.Everything_GetResultDateModified(i, ctypes.byref(date_buf)):
                mtime = _ft_to_ts(date_buf.value)

            rows.append((name, full, size, mtime or 0.0))

        elapsed = int((time.perf_counter() - t0) * 1000)
        return rows, n_total, elapsed


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: EverythingSearch | None = None


def get_search() -> EverythingSearch:
    global _instance
    if _instance is None:
        _instance = EverythingSearch()
    return _instance


# ── Query builder ─────────────────────────────────────────────────────────────
# Maps our UI filter values into Everything's native query syntax so filtering
# happens inside Everything (fast) rather than in Python (slow post-processing).

def build_query(
    text:      str,
    ext_list:  list[str] | None = None,   # ['.jpg', '.png', …]  None = all
    size_min:  int | None = None,          # bytes
    size_max:  int | None = None,          # bytes
    date_from: str | None = None,          # 'YYYY-MM-DD'
    date_to:   str | None = None,          # 'YYYY-MM-DD'
) -> str:
    """
    Build an Everything query string from UI filter state.

    Examples:
      build_query('report')              → 'report'
      build_query('', ext_list=['.jpg']) → 'ext:jpg'
      build_query('x', size_min=1048576) → 'x size:>=1mb'
    """
    parts: list[str] = []

    if text:
        parts.append(text)

    if ext_list:
        exts = ';'.join(e.lstrip('.').lower() for e in ext_list if e)
        if exts:
            parts.append(f'ext:{exts}')

    if size_min is not None and size_max is not None:
        parts.append(f'size:{_bytes_str(size_min)}..{_bytes_str(size_max)}')
    elif size_min is not None:
        parts.append(f'size:>={_bytes_str(size_min)}')
    elif size_max is not None:
        parts.append(f'size:<={_bytes_str(size_max)}')

    if date_from and date_to:
        parts.append(f'dm:{date_from}..{date_to}')
    elif date_from:
        parts.append(f'dm:>={date_from}')
    elif date_to:
        parts.append(f'dm:<={date_to}')

    return ' '.join(parts) if parts else '*'


def _bytes_str(n: int) -> str:
    """Convert byte count to an Everything size string (e.g. '1mb', '512kb')."""
    GB = 1_073_741_824
    MB = 1_048_576
    KB = 1_024
    if n >= GB and n % GB == 0:
        return f'{n // GB}gb'
    if n >= MB and n % MB == 0:
        return f'{n // MB}mb'
    if n >= KB and n % KB == 0:
        return f'{n // KB}kb'
    return str(n)
