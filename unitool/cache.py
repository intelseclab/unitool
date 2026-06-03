import os
import json
from datetime import datetime, timezone
from .platform_utils import data_dir

_DATA_DIR = data_dir('UniTool')
_HASH_CACHE_FILE = os.path.join(_DATA_DIR, 'hash_cache.json')
_SESSION_FILE    = os.path.join(_DATA_DIR, 'scan_session.json')


# ── Hash cache ────────────────────────────────────────────────────────────────

def load_hash_cache() -> dict:
    """Load {path: {size, mtime, partial?, full?}} from disk."""
    try:
        with open(_HASH_CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_hash_cache(cache: dict):
    """Persist the hash cache to disk."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_HASH_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f)
    except OSError:
        pass


# ── Session save / restore ────────────────────────────────────────────────────

def save_session(folders: list, file_type: str,
                 group_count: int, dupe_count: int,
                 bytes_saved: int, groups: list):
    """
    Save the last completed scan's groups to disk.

    groups format: [{reason_key: str, files: [file_dict, ...]}, ...]
    """
    data = {
        'timestamp':   datetime.now(timezone.utc).isoformat(),
        'folders':     list(folders),
        'file_type':   file_type,
        'group_count': group_count,
        'dupe_count':  dupe_count,
        'bytes_saved': bytes_saved,
        'groups':      groups,
    }
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(_SESSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def load_session() -> dict | None:
    """Return the saved session dict, or None if none exists / unreadable."""
    try:
        with open(_SESSION_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def clear_session():
    """Delete the saved session file."""
    try:
        os.remove(_SESSION_FILE)
    except (FileNotFoundError, OSError):
        pass
