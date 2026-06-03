import os
import sys
import shutil
import subprocess
import configparser
import json
from dataclasses import dataclass


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class BrowserProfile:
    browser: str        # 'Chrome' | 'Firefox' | 'Edge' | 'Brave' | 'Opera' | 'Vivaldi' | 'Safari'
    profile_name: str   # 'Default' | 'Profile 1' etc.
    profile_path: str   # Full path to profile directory


@dataclass
class BrowserDataItem:
    browser: str
    profile: str        # profile_name
    data_type: str      # 'history' | 'cookies' | 'cache' | 'downloads' | 'sessions' | 'autofill' | 'passwords' | 'form_data'
    path: str           # file or directory path
    size: int           # bytes
    description: str
    exists: bool
    deletable: bool
    safe_to_delete: bool  # False for passwords (show warning)
    method: str         # 'delete' | 'delete_dir' | 'sqlite_clear'


# ── Platform helpers ──────────────────────────────────────────────────────────

_IS_WIN   = sys.platform == 'win32'
_IS_MAC   = sys.platform == 'darwin'
_IS_LINUX = not _IS_WIN and not _IS_MAC

_HOME = os.path.expanduser('~')


def _dir_size(path: str) -> int:
    total = 0
    try:
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def fmt_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n:.1f} {unit}' if unit != 'B' else f'{n} B'
        n /= 1024
    return f'{n:.1f} PB'


# ── Browser base paths ────────────────────────────────────────────────────────

def _chromium_bases() -> dict[str, str]:
    if _IS_WIN:
        local = os.environ.get('LOCALAPPDATA', '')
        roaming = os.environ.get('APPDATA', '')
        return {
            'Chrome':  os.path.join(local,   'Google', 'Chrome', 'User Data'),
            'Edge':    os.path.join(local,   'Microsoft', 'Edge', 'User Data'),
            'Brave':   os.path.join(local,   'BraveSoftware', 'Brave-Browser', 'User Data'),
            'Vivaldi': os.path.join(local,   'Vivaldi', 'User Data'),
            'Opera':   os.path.join(roaming, 'Opera Software', 'Opera Stable'),
        }
    elif _IS_MAC:
        app_support = os.path.join(_HOME, 'Library', 'Application Support')
        return {
            'Chrome':  os.path.join(app_support, 'Google', 'Chrome'),
            'Edge':    os.path.join(app_support, 'Microsoft Edge'),
            'Brave':   os.path.join(app_support, 'BraveSoftware', 'Brave-Browser'),
            'Vivaldi': os.path.join(app_support, 'Vivaldi'),
            'Opera':   os.path.join(app_support, 'com.operasoftware.Opera'),
        }
    else:
        cfg = os.path.join(_HOME, '.config')
        return {
            'Chrome':  os.path.join(cfg, 'google-chrome'),
            'Edge':    os.path.join(cfg, 'microsoft-edge'),
            'Brave':   os.path.join(cfg, 'BraveSoftware', 'Brave-Browser'),
            'Vivaldi': os.path.join(cfg, 'vivaldi'),
            'Opera':   os.path.join(cfg, 'opera'),
        }


def _firefox_profiles_root() -> str:
    if _IS_WIN:
        return os.path.join(os.environ.get('APPDATA', _HOME), 'Mozilla', 'Firefox')
    elif _IS_MAC:
        return os.path.join(_HOME, 'Library', 'Application Support', 'Firefox')
    else:
        return os.path.join(_HOME, '.mozilla', 'firefox')


def _firefox_cache_root(profile_dir: str) -> str:
    """Return the Firefox cache2 directory for the given profile."""
    if _IS_WIN:
        # Cache may live in LocalAppData on Windows
        profile_leaf = os.path.basename(profile_dir)
        local_cache = os.path.join(
            os.environ.get('LOCALAPPDATA', _HOME),
            'Mozilla', 'Firefox', 'Profiles', profile_leaf, 'cache2',
        )
        if os.path.isdir(local_cache):
            return local_cache
    return os.path.join(profile_dir, 'cache2')


# ── Process names ─────────────────────────────────────────────────────────────

_BROWSER_PROCESSES: dict[str, str] = {
    'Chrome':  'chrome.exe'  if _IS_WIN else 'chrome',
    'Edge':    'msedge.exe'  if _IS_WIN else 'msedge',
    'Brave':   'brave.exe'   if _IS_WIN else 'brave',
    'Vivaldi': 'vivaldi.exe' if _IS_WIN else 'vivaldi',
    'Opera':   'opera.exe'   if _IS_WIN else 'opera',
    'Firefox': 'firefox.exe' if _IS_WIN else 'firefox',
    'Safari':  'Safari',
}


# ── Browser icons ─────────────────────────────────────────────────────────────

_BROWSER_ICONS: dict[str, str] = {
    'Chrome':  '\U0001f7e1',   # yellow circle
    'Firefox': '\U0001f98a',   # fox
    'Edge':    '\U0001f535',   # blue circle
    'Brave':   '\U0001f981',   # lion
    'Opera':   '\U0001f534',   # red circle
    'Vivaldi': '\U0001f3b5',   # musical note
    'Safari':  '\U0001f9ed',   # compass
}


def get_browser_icon(browser: str) -> str:
    return _BROWSER_ICONS.get(browser, '\U0001f310')  # globe


# ── Profile detection ─────────────────────────────────────────────────────────

def _chromium_profile_display_name(profile_path: str) -> str:
    """Read the profile display name from the Preferences JSON file."""
    prefs = os.path.join(profile_path, 'Preferences')
    try:
        with open(prefs, 'r', encoding='utf-8', errors='replace') as f:
            data = json.load(f)
        name = (
            data.get('profile', {}).get('name')
            or data.get('account_info', [{}])[0].get('full_name')
            or ''
        )
        if name:
            return name
    except Exception:
        pass
    return os.path.basename(profile_path)


def _chromium_profiles(user_data: str) -> list[BrowserProfile]:
    """List all Chromium profiles inside a User Data directory."""
    profiles: list[BrowserProfile] = []
    if not os.path.isdir(user_data):
        return profiles
    try:
        entries = os.listdir(user_data)
    except OSError:
        return profiles

    for name in entries:
        if name == 'Default' or (name.startswith('Profile ') and name[8:].isdigit()):
            full = os.path.join(user_data, name)
            if os.path.isdir(full):
                display = _chromium_profile_display_name(full)
                profiles.append(BrowserProfile(
                    browser='',  # filled by caller
                    profile_name=display if display != name else name,
                    profile_path=full,
                ))
    # Sort: Default first, then numerically
    def _sort_key(p: BrowserProfile) -> tuple:
        leaf = os.path.basename(p.profile_path)
        if leaf == 'Default':
            return (0, 0)
        try:
            return (1, int(leaf.split(' ', 1)[1]))
        except (IndexError, ValueError):
            return (1, 999)

    profiles.sort(key=_sort_key)
    return profiles


def _firefox_profiles(profiles_root: str) -> list[BrowserProfile]:
    """Parse Firefox profiles.ini and return all profiles."""
    profiles: list[BrowserProfile] = []
    ini_path = os.path.join(profiles_root, 'profiles.ini')
    if not os.path.isfile(ini_path):
        return profiles
    try:
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding='utf-8')
        for section in cfg.sections():
            if not section.lower().startswith('profile'):
                continue
            name = cfg.get(section, 'Name', fallback='')
            path = cfg.get(section, 'Path', fallback='')
            is_relative = cfg.getint(section, 'IsRelative', fallback=1)
            if not path:
                continue
            if is_relative:
                full_path = os.path.join(profiles_root, path)
            else:
                full_path = path
            full_path = os.path.normpath(full_path)
            if os.path.isdir(full_path):
                profiles.append(BrowserProfile(
                    browser='Firefox',
                    profile_name=name or os.path.basename(full_path),
                    profile_path=full_path,
                ))
    except Exception:
        pass
    return profiles


# ── Public API ────────────────────────────────────────────────────────────────

def detect_browsers() -> list[str]:
    """Return list of browser names detected on this system."""
    found: list[str] = []
    for browser, base in _chromium_bases().items():
        if os.path.isdir(base):
            found.append(browser)
    ff_root = _firefox_profiles_root()
    if os.path.isdir(ff_root):
        found.append('Firefox')
    # Safari (macOS only)
    if _IS_MAC:
        safari_path = os.path.join(_HOME, 'Library', 'Safari')
        if os.path.isdir(safari_path):
            found.append('Safari')
    return found


def get_profiles(browser: str) -> list[BrowserProfile]:
    """Return all profiles for the given browser."""
    if browser == 'Firefox':
        profiles = _firefox_profiles(_firefox_profiles_root())
        return profiles

    bases = _chromium_bases()
    if browser not in bases:
        return []
    user_data = bases[browser]
    profiles = _chromium_profiles(user_data)
    for p in profiles:
        p.browser = browser
    return profiles


# ── Per-profile data items ────────────────────────────────────────────────────

def _file_item(browser: str, profile: str, data_type: str,
               path: str, description: str,
               safe: bool = True,
               method: str = 'delete') -> BrowserDataItem:
    exists = os.path.isfile(path)
    size = os.path.getsize(path) if exists else 0
    return BrowserDataItem(
        browser=browser, profile=profile, data_type=data_type,
        path=path, size=size, description=description,
        exists=exists, deletable=exists, safe_to_delete=safe,
        method=method,
    )


def _dir_item(browser: str, profile: str, data_type: str,
              path: str, description: str,
              safe: bool = True) -> BrowserDataItem:
    exists = os.path.isdir(path)
    size = _dir_size(path) if exists else 0
    return BrowserDataItem(
        browser=browser, profile=profile, data_type=data_type,
        path=path, size=size, description=description,
        exists=exists, deletable=exists, safe_to_delete=safe,
        method='delete_dir',
    )


def _chromium_items(profile: BrowserProfile) -> list[BrowserDataItem]:
    """Build BrowserDataItems for a single Chromium profile."""
    b = profile.browser
    pn = profile.profile_name
    pp = profile.profile_path
    items: list[BrowserDataItem] = []

    # History (also covers downloads in the same file — we label separately)
    history = os.path.join(pp, 'History')
    items.append(_file_item(b, pn, 'history', history,
                            'Browsing history — URLs, titles, visit counts, timestamps.'))
    items.append(_file_item(b, pn, 'downloads', history,
                            'Download history stored in the History SQLite database.'))

    # Cookies
    items.append(_file_item(b, pn, 'cookies', os.path.join(pp, 'Cookies'),
                            'Browser cookies — site session tokens and persistent login data.'))

    # Cache directories
    cache_dirs = [
        (os.path.join(pp, 'Cache'),      'cache',       'Main HTTP response cache.'),
        (os.path.join(pp, 'Code Cache'), 'cache',       'JavaScript and WebAssembly compiled code cache.'),
        (os.path.join(pp, 'GPUCache'),   'cache',       'GPU shader and resource cache.'),
        (os.path.join(pp, 'Media Cache'),'media_cache', 'Cached media (audio/video) files.'),
    ]
    for path, dtype, desc in cache_dirs:
        items.append(_dir_item(b, pn, dtype, path, desc))

    # Session files
    for fname, desc in [
        ('Current Session', 'Current open tabs and session state.'),
        ('Current Tabs',    'Currently open tab URLs.'),
        ('Last Session',    'Session state from the previous browser session.'),
        ('Last Tabs',       'Tabs from the previous browser session.'),
    ]:
        items.append(_file_item(b, pn, 'sessions', os.path.join(pp, fname), desc))

    # Autofill / form data (Web Data — does NOT contain passwords)
    web_data = os.path.join(pp, 'Web Data')
    items.append(_file_item(b, pn, 'autofill', web_data,
                            'Autofill entries stored in the Web Data SQLite database.'))
    items.append(_file_item(b, pn, 'form_data', web_data,
                            'Form fill data stored in the Web Data SQLite database.'))

    # Passwords — safe_to_delete=False
    items.append(_file_item(b, pn, 'passwords', os.path.join(pp, 'Login Data'),
                            'Saved passwords (encrypted). Deleting this removes all saved logins.',
                            safe=False))

    return [i for i in items if i.exists]


def _firefox_items(profile: BrowserProfile) -> list[BrowserDataItem]:
    """Build BrowserDataItems for a single Firefox profile."""
    b = 'Firefox'
    pn = profile.profile_name
    pp = profile.profile_path
    items: list[BrowserDataItem] = []

    # History
    items.append(_file_item(b, pn, 'history', os.path.join(pp, 'places.sqlite'),
                            'Browsing history and bookmarks stored in places.sqlite.'))

    # Cookies
    items.append(_file_item(b, pn, 'cookies', os.path.join(pp, 'cookies.sqlite'),
                            'Firefox cookies database.'))

    # Cache
    cache_path = _firefox_cache_root(pp)
    items.append(_dir_item(b, pn, 'cache', cache_path,
                           'Firefox HTTP cache directory (cache2).'))

    # Form data
    items.append(_file_item(b, pn, 'form_data', os.path.join(pp, 'formhistory.sqlite'),
                            'Form autofill history stored in formhistory.sqlite.'))

    # Sessions
    for fname, desc in [
        ('sessionstore.jsonlz4', 'Firefox session store — open tabs and windows.'),
        ('sessionCheckpoints.json', 'Session checkpoint metadata.'),
    ]:
        items.append(_file_item(b, pn, 'sessions', os.path.join(pp, fname), desc))

    return [i for i in items if i.exists]


def scan_browser_data(browser: str | None = None) -> list[BrowserDataItem]:
    """
    Scan browser data for all detected browsers (or a specific one).
    Returns a flat list of BrowserDataItems for existing artifacts only.
    """
    targets = detect_browsers() if browser is None else [browser]
    results: list[BrowserDataItem] = []

    for bname in targets:
        try:
            profiles = get_profiles(bname)
            for profile in profiles:
                if bname == 'Firefox':
                    results.extend(_firefox_items(profile))
                else:
                    results.extend(_chromium_items(profile))
        except Exception:
            pass

    return results


# ── Browser running check ─────────────────────────────────────────────────────

def is_browser_running(browser: str) -> bool:
    """Return True if the browser process is currently running."""
    proc = _BROWSER_PROCESSES.get(browser)
    if not proc:
        return False
    try:
        if _IS_WIN:
            try:
                import ctypes
                import ctypes.wintypes as _bwt

                class _PE32W(ctypes.Structure):
                    _fields_ = [
                        ('dwSize',              ctypes.c_uint32),
                        ('cntUsage',            ctypes.c_uint32),
                        ('th32ProcessID',       ctypes.c_uint32),
                        ('th32DefaultHeapID',   ctypes.c_size_t),
                        ('th32ModuleID',        ctypes.c_uint32),
                        ('cntThreads',          ctypes.c_uint32),
                        ('th32ParentProcessID', ctypes.c_uint32),
                        ('pcPriClassBase',      ctypes.c_int32),
                        ('dwFlags',             ctypes.c_uint32),
                        ('szExeFile',           ctypes.c_wchar * 260),
                    ]

                _bk32 = ctypes.WinDLL('kernel32', use_last_error=True)
                _bk32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
                snap = _bk32.CreateToolhelp32Snapshot(0x00000002, 0)
                invalid = ctypes.c_size_t(-1).value
                if snap in (None, 0, invalid):
                    raise OSError('snapshot failed')
                target = proc.lower()
                entry  = _PE32W()
                entry.dwSize = ctypes.sizeof(_PE32W)
                found = False
                try:
                    if _bk32.Process32FirstW(snap, ctypes.byref(entry)):
                        if entry.szExeFile.lower() == target:
                            found = True
                        while not found and _bk32.Process32NextW(snap, ctypes.byref(entry)):
                            if entry.szExeFile.lower() == target:
                                found = True
                finally:
                    _bk32.CloseHandle(snap)
                return found
            except Exception:
                pass
            # fallback
            try:
                result = subprocess.run(
                    ['tasklist', '/FI', f'IMAGENAME eq {proc}', '/NH'],
                    capture_output=True, text=True, timeout=5,
                    creationflags=0x08000000,
                )
                return proc.lower() in result.stdout.lower()
            except Exception:
                return False
        else:
            result = subprocess.run(
                ['pgrep', '-x', proc],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
    except Exception:
        return False


# ── Clean ─────────────────────────────────────────────────────────────────────

def clean_browser_item(item: BrowserDataItem) -> tuple[bool, str]:
    """
    Clean a single BrowserDataItem.
    Returns (success, error_message).
    """
    if not item.deletable:
        return False, 'Item is not deletable'
    try:
        if item.method == 'delete':
            os.remove(item.path)
            return True, ''
        elif item.method == 'delete_dir':
            shutil.rmtree(item.path, ignore_errors=True)
            return True, ''
        elif item.method == 'sqlite_clear':
            # Not yet implemented — fall back to full file delete
            os.remove(item.path)
            return True, ''
        else:
            return False, f'Unknown method: {item.method}'
    except Exception as e:
        return False, str(e)


# ── Preview ───────────────────────────────────────────────────────────────────

_PREVIEW_MAX_BYTES = 32_768

def get_browser_item_preview(item: BrowserDataItem) -> str:
    """Return a human-readable preview string for the given item."""
    if not item.exists:
        return f'[Not found]\n\nPath: {item.path}'

    if item.data_type == 'passwords':
        return (
            f'Saved Passwords — {fmt_size(item.size)}\n'
            f'{"─"*50}\n'
            f'{item.path}\n\n'
            f'{item.description}\n\n'
            f'[Contents not shown — file contains encrypted credential data]'
        )

    if os.path.isdir(item.path):
        lines = [f'Directory: {item.path}', '']
        try:
            from datetime import datetime
            entries = sorted(os.scandir(item.path), key=lambda e: e.name.lower())
            lines.append(f'{len(entries)} item(s)  —  {fmt_size(item.size)} total')
            lines.append('─' * 60)
            for e in entries[:80]:
                try:
                    st = e.stat()
                    mtime = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')
                    lines.append(f'  {fmt_size(st.st_size):>10}   {mtime}   {e.name}')
                except OSError:
                    lines.append(f'  [unreadable]   {e.name}')
            if len(entries) > 80:
                lines.append(f'\n  … and {len(entries)-80} more items')
        except (OSError, PermissionError) as e:
            lines.append(f'[Permission denied: {e}]')
        return '\n'.join(lines)

    if os.path.isfile(item.path):
        try:
            with open(item.path, 'rb') as f:
                raw = f.read(_PREVIEW_MAX_BYTES)
            if b'\x00' in raw[:256]:
                return f'[Binary / SQLite file — {fmt_size(item.size)}]\n\nPath: {item.path}\n\n{item.description}'
            text = raw.decode('utf-8', errors='replace')
            is_partial = item.size > _PREVIEW_MAX_BYTES
            prefix = (
                f'{item.path}\n{"─"*60}\n'
                + (f'[Partial: first {fmt_size(_PREVIEW_MAX_BYTES)} of {fmt_size(item.size)}]\n\n'
                   if is_partial else '')
            )
            return prefix + text[:4096]
        except (OSError, PermissionError) as e:
            return f'[Cannot read file]\n\n{e}'

    return f'[Not accessible]\n\nPath: {item.path}'
