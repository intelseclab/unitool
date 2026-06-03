"""
UniTool/installer.py
Ninite-style app installer backend — uses winget as the package manager.
"""

import os
import sys
import re
import json
import subprocess
from dataclasses import dataclass, field
from typing import Callable

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

CATEGORIES: list[tuple[str, str, str]] = [
    ('browsers',      '🌐', 'inst_cat_browsers'),
    ('communication', '💬', 'inst_cat_communication'),
    ('media',         '🎵', 'inst_cat_media'),
    ('dev',           '🛠', 'inst_cat_dev'),
    ('utilities',     '🔧', 'inst_cat_utilities'),
    ('security',      '🔒', 'inst_cat_security'),
    ('gaming',        '🎮', 'inst_cat_gaming'),
    ('office',        '📄', 'inst_cat_office'),
]

# ---------------------------------------------------------------------------
# AppEntry dataclass
# ---------------------------------------------------------------------------

@dataclass
class AppEntry:
    winget_id: str
    name: str
    publisher: str
    category: str
    description: str
    installed: bool = False
    installed_version: str = ''
    available_version: str = ''
    has_update: bool = False


@dataclass
class InstalledEntry:
    """One row from `winget list` — not necessarily in our catalog."""
    winget_id: str
    name: str
    version: str
    available_version: str = ''
    has_update: bool = False
    source: str = ''

# ---------------------------------------------------------------------------
# Catalog data
# (category, winget_id, name, publisher, description)
# ---------------------------------------------------------------------------

_CATALOG_DATA: list[tuple[str, str, str, str, str]] = [
    # browsers
    ('browsers', 'Google.Chrome',      'Google Chrome', 'Google',   'Fast, secure web browser'),
    ('browsers', 'Mozilla.Firefox',    'Firefox',       'Mozilla',  'Free & open-source browser'),
    ('browsers', 'Brave.Brave',        'Brave',         'Brave',    'Privacy browser with built-in ad blocker'),
    ('browsers', 'Opera.Opera',        'Opera',         'Opera',    'Browser with built-in VPN'),
    ('browsers', 'Vivaldi.Vivaldi',    'Vivaldi',       'Vivaldi',  'Highly customizable browser'),

    # communication
    ('communication', 'Discord.Discord',           'Discord',  'Discord',   'Voice, video and text chat'),
    ('communication', 'SlackTechnologies.Slack',   'Slack',    'Slack',     'Team messaging and collaboration'),
    ('communication', 'Zoom.Zoom',                 'Zoom',     'Zoom',      'Video conferencing'),
    ('communication', 'Telegram.TelegramDesktop',  'Telegram', 'Telegram',  'Secure messaging app'),
    ('communication', 'OpenWhisperSystems.Signal', 'Signal',   'Signal',    'Private encrypted messaging'),
    ('communication', 'Microsoft.Skype',           'Skype',    'Microsoft', 'Video calling and messaging'),
    ('communication', 'WhatsApp.WhatsApp',         'WhatsApp', 'Meta',      'Messaging and calls'),

    # media
    ('media', 'VideoLAN.VLC',         'VLC',        'VideoLAN',  'Free multimedia player'),
    ('media', 'Spotify.Spotify',      'Spotify',    'Spotify',   'Music streaming service'),
    ('media', 'OBSProject.OBSStudio', 'OBS Studio', 'OBS',       'Screen recording and live streaming'),
    ('media', 'HandBrake.HandBrake',  'HandBrake',  'HandBrake', 'Open-source video transcoder'),
    ('media', 'Audacity.Audacity',    'Audacity',   'Audacity',  'Free audio editor'),
    ('media', 'GIMP.GIMP',            'GIMP',       'GIMP',      'Free image editor'),
    ('media', 'Inkscape.Inkscape',    'Inkscape',   'Inkscape',  'Free vector graphics editor'),
    ('media', 'clsid2.mpc-hc',        'MPC-HC',     'clsid2',    'Lightweight media player'),

    # dev
    ('dev', 'Microsoft.VisualStudioCode', 'VS Code',        'Microsoft',         'Lightweight code editor'),
    ('dev', 'Anysphere.Cursor',           'Cursor',         'Anysphere',         'AI-powered code editor'),
    ('dev', 'Git.Git',                    'Git',            'Git',               'Distributed version control'),
    ('dev', 'OpenJS.NodeJS.LTS',          'Node.js LTS',    'OpenJS',            'JavaScript runtime'),
    ('dev', 'Python.Python.3',            'Python 3',       'Python',            'Programming language'),
    ('dev', 'Notepad++.Notepad++',        'Notepad++',      'Don Ho',            'Advanced text editor'),
    ('dev', 'WinMerge.WinMerge',          'WinMerge',       'WinMerge',          'Compare and merge files'),
    ('dev', 'TimKosse.FileZilla.Client',  'FileZilla',      'Tim Kosse',         'FTP/SFTP client'),
    ('dev', 'Docker.DockerDesktop',       'Docker Desktop', 'Docker',            'Container platform'),
    ('dev', 'Postman.Postman',            'Postman',        'Postman',           'API testing tool'),
    ('dev', 'EclipseFoundation.EclipseIDE', 'Eclipse IDE',  'Eclipse Foundation','IDE for Java development'),
    ('dev', 'Microsoft.PowerShell',       'PowerShell 7',   'Microsoft',         'Modern cross-platform shell'),
    ('dev', 'WinSCP.WinSCP',              'WinSCP',         'WinSCP',            'SFTP and FTP client'),
    ('dev', 'PuTTY.PuTTY',               'PuTTY',          'PuTTY',             'SSH and Telnet client'),

    # utilities
    ('utilities', 'voidtools.Everything',              'Everything',      'voidtools',         'Instant file search'),
    ('utilities', 'ShareX.ShareX',                     'ShareX',          'ShareX',            'Screen capture and recording'),
    ('utilities', '7zip.7zip',                         '7-Zip',           '7-Zip',             'Free file archiver'),
    ('utilities', 'JAMSoftware.TreeSize.Free',         'TreeSize Free',   'JAM',               'Disk space analyzer'),
    ('utilities', 'CrystalDewWorld.CrystalDiskInfo',   'CrystalDiskInfo', 'Crystal Dew World', 'Disk health monitor'),
    ('utilities', 'REALiX.HWiNFO',                    'HWiNFO',          'REALiX',            'Hardware information'),
    ('utilities', 'CPUID.CPU-Z',                       'CPU-Z',           'CPUID',             'CPU information tool'),
    ('utilities', 'WinDirStat.WinDirStat',             'WinDirStat',      'WinDirStat',        'Disk usage visualizer'),
    ('utilities', 'Greenshot.Greenshot',               'Greenshot',       'Greenshot',         'Screenshot tool'),
    ('utilities', 'Giorgiotani.Peazip',                'PeaZip',          'Giorgio Tani',      'Free file archiver'),

    # security
    ('security', 'Bitwarden.Bitwarden',         'Bitwarden',    'Bitwarden',    'Open-source password manager'),
    ('security', 'KeePass.KeePass',             'KeePass',      'D. Reichl',    'Secure local password manager'),
    ('security', 'IDRIX.VeraCrypt',             'VeraCrypt',    'IDRIX',        'Disk encryption tool'),
    ('security', 'ProtonTechnologies.ProtonVPN', 'ProtonVPN',   'Proton',       'Secure VPN service'),
    ('security', 'Malwarebytes.Malwarebytes',   'Malwarebytes', 'Malwarebytes', 'Anti-malware protection'),

    # gaming
    ('gaming', 'Valve.Steam',                 'Steam',      'Valve',    'Game distribution platform'),
    ('gaming', 'EpicGames.EpicGamesLauncher', 'Epic Games', 'Epic',     'Game store and launcher'),
    ('gaming', 'GOG.Galaxy',                  'GOG Galaxy', 'GOG',      'DRM-free game platform'),
    ('gaming', 'Overwolf.Overwolf',           'Overwolf',   'Overwolf', 'Gaming overlay platform'),

    # office
    ('office', 'TheDocumentFoundation.LibreOffice', 'LibreOffice',   'TDF',           'Free office suite'),
    ('office', 'SumatraPDF.SumatraPDF',             'Sumatra PDF',   'Sumatra',       'Lightweight PDF reader'),
    ('office', 'calibre.calibre',                   'Calibre',       'Kovid Goyal',   'E-book manager'),
    ('office', 'Obsidian.Obsidian',                 'Obsidian',      'Obsidian',      'Knowledge base & notes'),
    ('office', 'Notion.Notion',                     'Notion',        'Notion',        'All-in-one workspace'),
    ('office', 'geek-software.PDF24Creator',        'PDF24 Creator', 'Geek Software', 'PDF tools suite'),
]

# ---------------------------------------------------------------------------
# Catalog factory
# ---------------------------------------------------------------------------

def get_catalog() -> list[AppEntry]:
    """Return a fresh list of AppEntry objects from the built-in catalog."""
    return [
        AppEntry(
            winget_id=winget_id,
            name=name,
            publisher=publisher,
            category=category,
            description=description,
        )
        for category, winget_id, name, publisher, description in _CATALOG_DATA
    ]

# ---------------------------------------------------------------------------
# winget helpers
# ---------------------------------------------------------------------------

_CNW = 0x08000000  # CREATE_NO_WINDOW


def is_winget_available() -> bool:
    """Return True if winget is available on this system."""
    try:
        r = subprocess.run(
            ['winget', '--version'],
            capture_output=True,
            timeout=5,
            creationflags=_CNW,
        )
        return r.returncode == 0
    except Exception:
        return False


def _run_winget(*args, timeout: int = 60) -> tuple[int, str]:
    """Run winget and return (returncode, combined_output)."""
    try:
        r = subprocess.run(
            ['winget'] + list(args),
            capture_output=True,
            timeout=timeout,
            creationflags=_CNW,
        )
        out = r.stdout.decode('utf-8', errors='replace')
        err = r.stderr.decode('utf-8', errors='replace')
        return r.returncode, out + err
    except Exception as e:
        return -1, str(e)


# ---------------------------------------------------------------------------
# Text-table parser helper
# ---------------------------------------------------------------------------

def _parse_winget_table(
    output: str,
    id_col: str = 'Id',
    version_col: str = 'Version',
    extra_col: str | None = None,
) -> dict[str, str]:
    """
    Parse a winget text table and return {id.lower(): version}.

    If extra_col is given (e.g. 'Available'), return {id.lower(): extra_value}
    for rows where the 4th column has a real value. Language-independent.
    """
    result: dict[str, str] = {}
    for r in _winget_rows(output):
        key = r['id'].lower()
        if extra_col:
            val = r['extra']
            if val and val not in ('-', 'Unknown', ''):
                result[key] = val
        elif r['version']:
            result[key] = r['version']
    return result


# ---------------------------------------------------------------------------
# _get_installed
# ---------------------------------------------------------------------------

def _get_installed() -> dict[str, str]:
    """
    Return {winget_id.lower(): installed_version} for all installed packages.

    Tries JSON output first (winget 1.2+), falls back to text table parsing.
    """
    # --- JSON attempt ---
    rc, out = _run_winget(
        'list',
        '--source', 'winget',
        '--accept-source-agreements',
        '--disable-interactivity',
        '--output', 'json',
        timeout=90,
    )
    if rc == 0 and out.strip():
        try:
            data = json.loads(out)
            # winget JSON schema: list of objects with "Id" and "Version"
            result: dict[str, str] = {}
            packages = data if isinstance(data, list) else data.get('Sources', [])
            if isinstance(packages, list) and packages and isinstance(packages[0], dict):
                # Flat list of package objects
                for pkg in packages:
                    pkg_id = pkg.get('Id', '')
                    version = pkg.get('Version', '')
                    if pkg_id:
                        result[pkg_id.lower()] = version
                return result
            # Nested sources format
            for source in packages:
                for pkg in source.get('Packages', []):
                    pkg_id = pkg.get('Id', '')
                    version = pkg.get('Version', '')
                    if pkg_id:
                        result[pkg_id.lower()] = version
            return result
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # --- Text table fallback ---
    rc, out = _run_winget(
        'list',
        '--source', 'winget',
        '--accept-source-agreements',
        '--disable-interactivity',
        timeout=90,
    )
    if rc != 0 and not out.strip():
        return {}
    return _parse_winget_table(out, id_col='Id', version_col='Version')


# ---------------------------------------------------------------------------
# _get_upgradeable
# ---------------------------------------------------------------------------

def _get_upgradeable() -> dict[str, str]:
    """
    Return {winget_id.lower(): available_version} for packages with updates.
    """
    rc, out = _run_winget(
        'upgrade',
        '--include-unknown',
        '--accept-source-agreements',
        '--disable-interactivity',
        timeout=120,
    )
    if not out.strip():
        return {}
    return _parse_winget_table(out, id_col='Id', version_col='Version', extra_col='Available')


# ---------------------------------------------------------------------------
# refresh_status
# ---------------------------------------------------------------------------

def refresh_status(apps: list[AppEntry]) -> None:
    """Update installed / has_update on each AppEntry in-place."""
    installed = _get_installed()       # {winget_id.lower(): installed_version}
    upgradeable = _get_upgradeable()   # {winget_id.lower(): available_version}

    for app in apps:
        key = app.winget_id.lower()
        if key in installed:
            app.installed = True
            app.installed_version = installed[key]
        else:
            app.installed = False
            app.installed_version = ''
        if key in upgradeable:
            app.has_update = True
            app.available_version = upgradeable[key]
        else:
            app.has_update = False
            app.available_version = ''


# ---------------------------------------------------------------------------
# get_all_installed_apps  (JSON-first, ANSI-stripped text fallback)
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHFJA-Za-z]')


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)


def _winget_rows(out: str) -> list[dict]:
    """
    Language-independent parser for any winget text table (search / list / upgrade).

    Anchors on the dashed separator line (identical in every UI language) instead
    of the localized header words, then maps columns by position order:
        col0 = Name, col1 = Id, col2 = Version,
        col3 = 4th column (Match for search / Available for list) when present,
        last = Source.
    Returns [{'name','id','version','extra','source'}].
    """
    lines = _strip_ansi(out).splitlines()

    # Find the separator line — a long run of dashes / box-drawing chars.
    sep_idx = -1
    for i, line in enumerate(lines):
        s = line.strip().replace(' ', '')
        if len(s) >= 10 and all(c in '-─—' for c in s):
            sep_idx = i
            break
    if sep_idx < 1:
        return []

    header = lines[sep_idx - 1]
    starts = [m.start() for m in re.finditer(r'\S+', header)]
    if len(starts) < 3:
        return []
    n = len(starts)

    def col(line: str, idx: int) -> str:
        if idx < 0 or idx >= n:
            return ''
        a = starts[idx]
        b = starts[idx + 1] if idx + 1 < n else len(line) + 1
        if a >= len(line):
            return ''
        return line[a:b].strip()

    rows: list[dict] = []
    for line in lines[sep_idx + 1:]:
        if not line.strip() or '\x1b' in line:
            continue
        pkg_id = col(line, 1)
        if not pkg_id:
            continue
        rows.append({
            'name':    col(line, 0) or pkg_id,
            'id':      pkg_id,
            'version': col(line, 2),
            'extra':   col(line, 3) if n >= 5 else '',
            'source':  col(line, n - 1) if n >= 4 else '',
        })
    return rows


def _parse_installed_text(out: str) -> list[InstalledEntry]:
    """Parse 'winget list' text-table output into InstalledEntry objects."""
    result: list[InstalledEntry] = []
    for r in _winget_rows(out):
        avail   = r['extra']
        has_upd = bool(avail and avail not in ('-', 'Unknown', ''))
        result.append(InstalledEntry(
            winget_id=r['id'],
            name=r['name'],
            version=r['version'],
            available_version=avail if has_upd else '',
            has_update=has_upd,
            source=r['source'],
        ))
    return result


def get_all_installed_apps() -> list[InstalledEntry]:
    """Return every package visible in `winget list`."""
    # ── JSON attempt (stdout only — stderr carries progress-bar noise) ──────
    try:
        r = subprocess.run(
            ['winget', 'list',
             '--accept-source-agreements', '--disable-interactivity',
             '--output', 'json'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=90,
            creationflags=_CNW,
        )
        raw = _strip_ansi(r.stdout.decode('utf-8', errors='replace')).strip()
        # winget sometimes prepends progress lines before the JSON array
        json_start = raw.find('[')
        if json_start == -1:
            json_start = raw.find('{')
        if r.returncode == 0 and json_start != -1:
            data = json.loads(raw[json_start:])
            pkgs: list[dict] = []
            if isinstance(data, list):
                pkgs = data
            elif isinstance(data, dict):
                for src in data.get('Sources', []):
                    pkgs.extend(src.get('Packages', []))
            result = [
                InstalledEntry(
                    winget_id=p['Id'],
                    name=p.get('Name', p['Id']),
                    version=p.get('Version', ''),
                    source=p.get('Source', ''),
                )
                for p in pkgs if p.get('Id')
            ]
            if result:
                return result
    except Exception:
        pass

    # ── Text-table fallback ──────────────────────────────────────────────────
    _rc, out = _run_winget(
        'list',
        '--accept-source-agreements',
        '--disable-interactivity',
        timeout=90,
    )
    return _parse_installed_text(_strip_ansi(out))


# ---------------------------------------------------------------------------
# winget_search
# ---------------------------------------------------------------------------

def winget_search(query: str, limit: int = 60) -> list[tuple[str, str, str, str, str]]:
    """
    Run 'winget search <query>' and return
    [(winget_id, name, version, match, source), ...].
    `match` is the Match-column text (e.g. 'Tag: microsoft-teams') or ''.
    Returns an empty list on error or no results.
    """
    _rc, out = _run_winget(
        'search', query,
        '--accept-source-agreements',
        '--disable-interactivity',
        timeout=30,
    )

    results: list[tuple[str, str, str, str, str]] = []
    for r in _winget_rows(out):
        results.append((r['id'], r['name'], r['version'], r['extra'], r['source'] or 'winget'))
        if len(results) >= limit:
            break
    return results


# ---------------------------------------------------------------------------
# install_app
# ---------------------------------------------------------------------------

def install_app(
    app: AppEntry,
    action: str = 'install',
    progress_cb: Callable[[str, int, str], None] | None = None,
) -> tuple[bool, str]:
    """
    Run 'winget install' or 'winget upgrade' for the given app.

    progress_cb(status_msg, pct, raw_line) — called with each output line.
    pct=-1 means indeterminate.

    Returns (success, error_message).
    """
    cmd = [
        'winget', action,
        '--id', app.winget_id, '-e',
        '--silent',
        '--accept-package-agreements',
        '--accept-source-agreements',
        '--disable-interactivity',
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=_CNW,
        )
        last_line = ''
        for raw in proc.stdout:
            line = raw.decode('utf-8', errors='replace').strip()
            if not line:
                continue
            last_line = line
            pct = -1
            m = re.search(r'(\d+)\s*%', line)
            if m:
                pct = int(m.group(1))
            status = 'Downloading…' if 'Downloading' in line else line[:80]
            if progress_cb:
                progress_cb(status, pct, line)
        proc.wait()
        if proc.returncode == 0:
            return True, ''
        return False, last_line or f'winget exited with code {proc.returncode}'
    except Exception as exc:
        return False, str(exc)
