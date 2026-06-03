import os
import sys
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class JunkItem:
    category: str     # 'temp' | 'windows' | 'browser_cache' | 'crash' | 'logs'
    label: str
    path: str
    size: int
    description: str
    exists: bool
    deletable: bool
    method: str       # 'delete_dir_contents' | 'delete_dir' | 'delete' | 'command'
    command: str = field(default='')  # only for method='command'


# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} PB'


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


def _dir_item_count(path: str) -> int:
    """Count files + subdirs directly inside path (non-recursive)."""
    try:
        return sum(1 for _ in os.scandir(path))
    except OSError:
        return 0


def _dir_file_count_recursive(path: str) -> int:
    count = 0
    try:
        for _, _, files in os.walk(path):
            count += len(files)
    except OSError:
        pass
    return count


# ── Temp files ────────────────────────────────────────────────────────────────

def scan_temp_files() -> list[JunkItem]:
    items: list[JunkItem] = []

    if sys.platform == 'win32':
        roots = [
            os.environ.get('TEMP', os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Temp')),
            os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Temp'),
        ]
        seen = set()
        for root in roots:
            root = os.path.normpath(root)
            if root in seen or not os.path.isdir(root):
                continue
            seen.add(root)
            # Root temp dir as a whole (delete contents, keep folder)
            count = _dir_item_count(root)
            size  = _dir_size(root)
            items.append(JunkItem(
                category='temp',
                label=f'{os.path.basename(root)}  ({count} items)',
                path=root,
                size=size,
                description=f'Windows temporary files folder. Contains {count} items '
                            f'({fmt_size(size)}). Deleting contents is safe; the folder itself is kept.',
                exists=count > 0,
                deletable=count > 0,
                method='delete_dir_contents',
            ))
            # Each immediate subdirectory as its own item
            try:
                for entry in sorted(os.scandir(root), key=lambda e: e.name.lower()):
                    if entry.is_dir(follow_symlinks=False):
                        sub_size  = _dir_size(entry.path)
                        sub_count = _dir_file_count_recursive(entry.path)
                        items.append(JunkItem(
                            category='temp',
                            label=f'{entry.name}  ({sub_count} files)',
                            path=entry.path,
                            size=sub_size,
                            description=f'Temporary subdirectory: {entry.path}',
                            exists=True,
                            deletable=True,
                            method='delete_dir',
                        ))
            except (OSError, PermissionError):
                pass

    elif sys.platform == 'darwin':
        home = os.path.expanduser('~')
        for root, desc in [
            ('/tmp', 'System temporary files'),
            (os.path.join(home, 'Library', 'Caches'), 'User application caches'),
        ]:
            if not os.path.isdir(root):
                continue
            count = _dir_item_count(root)
            size  = _dir_size(root)
            items.append(JunkItem(
                category='temp',
                label=f'{os.path.basename(root)}  ({count} items)',
                path=root,
                size=size,
                description=desc,
                exists=count > 0,
                deletable=count > 0,
                method='delete_dir_contents',
            ))

    else:
        home = os.path.expanduser('~')
        for root, desc in [
            ('/tmp', 'System temporary files'),
            (os.path.join(home, '.cache'), 'User application cache directory'),
        ]:
            if not os.path.isdir(root):
                continue
            count = _dir_item_count(root)
            size  = _dir_size(root)
            items.append(JunkItem(
                category='temp',
                label=f'{os.path.basename(root)}  ({count} items)',
                path=root,
                size=size,
                description=desc,
                exists=count > 0,
                deletable=count > 0,
                method='delete_dir_contents',
            ))

    return items


# ── Windows junk ──────────────────────────────────────────────────────────────

def scan_windows_junk() -> list[JunkItem]:
    items: list[JunkItem] = []

    if sys.platform == 'win32':
        windir    = os.environ.get('WINDIR', r'C:\Windows')
        localapp  = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))

        entries = [
            # (path, label, description, method, deletable_override)
            (
                os.path.join(windir, 'SoftwareDistribution', 'Download'),
                'Windows Update Cache',
                'Windows Update downloaded files. Safe to delete — Windows will re-download '
                'updates as needed. Can reclaim several GB on older systems.',
                'delete_dir_contents',
                True,
            ),
            (
                os.path.join(windir, 'SoftwareDistribution', 'DeliveryOptimization'),
                'Delivery Optimization Cache',
                'Peer-to-peer Windows Update delivery cache. Windows manages this automatically '
                'but it can grow large.',
                'delete_dir_contents',
                True,
            ),
            (
                os.path.join(localapp, 'Microsoft', 'Windows', 'WER', 'ReportArchive'),
                'WER Report Archive',
                'Windows Error Reporting archived crash reports. Safe to delete.',
                'delete_dir_contents',
                True,
            ),
            (
                os.path.join(localapp, 'Microsoft', 'Windows', 'WER', 'ReportQueue'),
                'WER Report Queue',
                'Windows Error Reporting queued crash reports awaiting upload. Safe to delete.',
                'delete_dir_contents',
                True,
            ),
            (
                os.path.join(localapp, 'D3DSCache'),
                'DirectX Shader Cache',
                'GPU shader compilation cache for DirectX. Windows rebuilds it automatically.',
                'delete_dir_contents',
                True,
            ),
            (
                os.path.join(windir, 'Logs', 'DISM'),
                'DISM Logs',
                'Deployment Image Servicing and Management tool logs. Safe to delete.',
                'delete_dir_contents',
                True,
            ),
        ]

        for path, label, desc, method, deletable_override in entries:
            if not os.path.isdir(path):
                items.append(JunkItem(
                    category='windows', label=label, path=path,
                    size=0, description=desc,
                    exists=False, deletable=False, method=method,
                ))
                continue
            count = _dir_item_count(path)
            size  = _dir_size(path)
            items.append(JunkItem(
                category='windows',
                label=f'{label}  ({count} items)',
                path=path,
                size=size,
                description=desc,
                exists=count > 0,
                deletable=deletable_override and count > 0,
                method=method,
            ))

    elif sys.platform == 'darwin':
        home = os.path.expanduser('~')
        logs_dir = os.path.join(home, 'Library', 'Logs')
        if os.path.isdir(logs_dir):
            count = _dir_item_count(logs_dir)
            size  = _dir_size(logs_dir)
            items.append(JunkItem(
                category='windows',
                label=f'Application Logs  ({count} items)',
                path=logs_dir,
                size=size,
                description='macOS application log files in ~/Library/Logs.',
                exists=count > 0,
                deletable=count > 0,
                method='delete_dir_contents',
            ))

    else:
        var_log = '/var/log'
        if os.path.isdir(var_log):
            count = _dir_item_count(var_log)
            size  = _dir_size(var_log)
            items.append(JunkItem(
                category='windows',
                label=f'System Logs  ({count} items)',
                path=var_log,
                size=size,
                description='/var/log — system log files. Some may require root to delete.',
                exists=count > 0,
                deletable=False,  # need root; mark manual
                method='delete_dir_contents',
            ))

    return items


# ── Browser caches ────────────────────────────────────────────────────────────

def _chromium_cache_dirs() -> list[tuple[str, str, str]]:
    """
    Return (browser_name, profile_label, cache_subdir_path) tuples
    for all Chromium-based browsers found on the system.
    """
    home     = os.path.expanduser('~')
    results: list[tuple[str, str, str]] = []

    if sys.platform == 'win32':
        local  = os.environ.get('LOCALAPPDATA', home)
        roaming = os.environ.get('APPDATA', home)
        browser_roots = [
            ('Chrome',   os.path.join(local,   'Google',    'Chrome',     'User Data')),
            ('Edge',     os.path.join(local,   'Microsoft', 'Edge',       'User Data')),
            ('Brave',    os.path.join(local,   'BraveSoftware', 'Brave-Browser', 'User Data')),
            ('Vivaldi',  os.path.join(local,   'Vivaldi',   'User Data')),
            ('Opera',    os.path.join(roaming, 'Opera Software', 'Opera Stable')),
            ('Opera GX', os.path.join(roaming, 'Opera Software', 'Opera GX Stable')),
        ]
    elif sys.platform == 'darwin':
        app_support = os.path.join(home, 'Library', 'Application Support')
        browser_roots = [
            ('Chrome',  os.path.join(app_support, 'Google', 'Chrome')),
            ('Edge',    os.path.join(app_support, 'Microsoft Edge')),
            ('Brave',   os.path.join(app_support, 'BraveSoftware', 'Brave-Browser')),
            ('Vivaldi', os.path.join(app_support, 'Vivaldi')),
            ('Opera',   os.path.join(app_support, 'com.operasoftware.Opera')),
        ]
    else:
        config = os.environ.get('XDG_CONFIG_HOME', os.path.join(home, '.config'))
        browser_roots = [
            ('Chrome',  os.path.join(config, 'google-chrome')),
            ('Chromium',os.path.join(config, 'chromium')),
            ('Edge',    os.path.join(config, 'microsoft-edge')),
            ('Brave',   os.path.join(config, 'BraveSoftware', 'Brave-Browser')),
            ('Vivaldi', os.path.join(config, 'vivaldi')),
            ('Opera',   os.path.join(config, 'opera')),
        ]

    cache_subdirs = ('Cache', 'Code Cache', 'GPUCache')

    for browser_name, user_data_dir in browser_roots:
        if not os.path.isdir(user_data_dir):
            continue
        # Find profiles: Default + Profile N
        try:
            profile_dirs = []
            for entry in os.scandir(user_data_dir):
                if entry.is_dir(follow_symlinks=False):
                    n = entry.name
                    if n == 'Default' or n.startswith('Profile'):
                        profile_dirs.append(entry.path)
            # Opera stores caches directly in user_data_dir (no User Data subdir)
            # also handle the flat layout used by Opera/Opera GX
            if not profile_dirs:
                profile_dirs = [user_data_dir]
        except OSError:
            continue

        for profile_path in profile_dirs:
            profile_label = os.path.basename(profile_path)
            for sub in cache_subdirs:
                cache_path = os.path.join(profile_path, sub)
                if os.path.isdir(cache_path):
                    results.append((browser_name, profile_label, cache_path))

    return results


def scan_browser_caches() -> list[JunkItem]:
    items: list[JunkItem] = []
    home = os.path.expanduser('~')

    # Chromium-based browsers
    for browser_name, profile_label, cache_path in _chromium_cache_dirs():
        count = _dir_item_count(cache_path)
        size  = _dir_size(cache_path)
        sub   = os.path.basename(cache_path)
        if profile_label in ('Default', browser_name):
            label = f'{browser_name} {sub}'
        else:
            label = f'{browser_name} {sub} ({profile_label})'
        items.append(JunkItem(
            category='browser_cache',
            label=f'{label}  ({count} files)',
            path=cache_path,
            size=size,
            description=f'{browser_name} browser {sub.lower()}. '
                        f'Safe to delete — the browser will rebuild it on next use.',
            exists=count > 0,
            deletable=count > 0,
            method='delete_dir_contents',
        ))

    # Firefox profiles
    if sys.platform == 'win32':
        ff_profiles_dir = os.path.join(
            os.environ.get('APPDATA', home), 'Mozilla', 'Firefox', 'Profiles',
        )
    elif sys.platform == 'darwin':
        ff_profiles_dir = os.path.join(
            home, 'Library', 'Application Support', 'Firefox', 'Profiles',
        )
    else:
        ff_profiles_dir = os.path.join(home, '.mozilla', 'firefox')

    if os.path.isdir(ff_profiles_dir):
        try:
            for entry in os.scandir(ff_profiles_dir):
                if not entry.is_dir(follow_symlinks=False):
                    continue
                cache2 = os.path.join(entry.path, 'cache2')
                if os.path.isdir(cache2):
                    count = _dir_file_count_recursive(cache2)
                    size  = _dir_size(cache2)
                    items.append(JunkItem(
                        category='browser_cache',
                        label=f'Firefox Cache ({entry.name})  ({count} files)',
                        path=cache2,
                        size=size,
                        description=f'Firefox cache2 directory for profile {entry.name}. '
                                    f'Safe to delete — Firefox rebuilds it on next use.',
                        exists=count > 0,
                        deletable=count > 0,
                        method='delete_dir_contents',
                    ))
        except OSError:
            pass

    return items


# ── Crash dumps ───────────────────────────────────────────────────────────────

def scan_crash_dumps() -> list[JunkItem]:
    items: list[JunkItem] = []

    if sys.platform == 'win32':
        localapp = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
        windir   = os.environ.get('WINDIR', r'C:\Windows')

        dump_paths = [
            (
                os.path.join(localapp, 'CrashDumps'),
                'User Crash Dumps',
                'Application crash dump files (.dmp) collected by Windows Error Reporting.',
            ),
            (
                os.path.join(localapp, 'Microsoft', 'Windows', 'WER', 'Temp'),
                'WER Temp',
                'Windows Error Reporting temporary staging files.',
            ),
            (
                os.path.join(windir, 'Minidump'),
                'Windows Minidumps',
                'Kernel / BSOD minidump files. Useful for debugging blue-screens; '
                'safe to delete once resolved.',
            ),
        ]

        for path, label, desc in dump_paths:
            if not os.path.isdir(path):
                items.append(JunkItem(
                    category='crash', label=label, path=path,
                    size=0, description=desc,
                    exists=False, deletable=False, method='delete_dir_contents',
                ))
                continue
            count = _dir_file_count_recursive(path)
            size  = _dir_size(path)
            items.append(JunkItem(
                category='crash',
                label=f'{label}  ({count} files)',
                path=path,
                size=size,
                description=desc,
                exists=count > 0,
                deletable=count > 0,
                method='delete_dir_contents',
            ))

    elif sys.platform == 'darwin':
        diag = os.path.join(os.path.expanduser('~'), 'Library', 'Logs', 'DiagnosticReports')
        if os.path.isdir(diag):
            count = _dir_file_count_recursive(diag)
            size  = _dir_size(diag)
            items.append(JunkItem(
                category='crash',
                label=f'Diagnostic Reports  ({count} files)',
                path=diag,
                size=size,
                description='macOS application crash and spin diagnostic reports.',
                exists=count > 0,
                deletable=count > 0,
                method='delete_dir_contents',
            ))

    else:
        var_crash = '/var/crash'
        if os.path.isdir(var_crash):
            count = _dir_file_count_recursive(var_crash)
            size  = _dir_size(var_crash)
            items.append(JunkItem(
                category='crash',
                label=f'Crash Reports  ({count} files)',
                path=var_crash,
                size=size,
                description='Linux kernel and application crash dump files.',
                exists=count > 0,
                deletable=count > 0,
                method='delete_dir_contents',
            ))

    return items


# ── Log files ─────────────────────────────────────────────────────────────────

def scan_log_files() -> list[JunkItem]:
    items: list[JunkItem] = []

    if sys.platform == 'win32':
        windir   = os.environ.get('WINDIR', r'C:\Windows')
        temp_dir = os.environ.get('TEMP', os.path.join(windir, 'Temp'))
        localapp = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))

        # Windows\Logs subdirectories
        win_logs = os.path.join(windir, 'Logs')
        if os.path.isdir(win_logs):
            try:
                for entry in sorted(os.scandir(win_logs), key=lambda e: e.name.lower()):
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    count = _dir_file_count_recursive(entry.path)
                    size  = _dir_size(entry.path)
                    if size == 0:
                        continue
                    items.append(JunkItem(
                        category='logs',
                        label=f'Windows Logs\\{entry.name}  ({count} files)',
                        path=entry.path,
                        size=size,
                        description=f'Windows log subdirectory: {entry.path}',
                        exists=count > 0,
                        deletable=count > 0,
                        method='delete_dir_contents',
                    ))
            except (OSError, PermissionError):
                pass

        # %TEMP%\*.log files
        log_files: list[tuple[str, int]] = []
        try:
            for entry in os.scandir(temp_dir):
                if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith('.log'):
                    try:
                        sz = entry.stat().st_size
                        log_files.append((entry.path, sz))
                    except OSError:
                        pass
        except (OSError, PermissionError):
            pass

        if log_files:
            total_log_size = sum(s for _, s in log_files)
            items.append(JunkItem(
                category='logs',
                label=f'Temp Log Files  ({len(log_files)} files)',
                path=temp_dir,
                size=total_log_size,
                description=f'{len(log_files)} .log files in %TEMP% totalling {fmt_size(total_log_size)}.',
                exists=True,
                deletable=True,
                method='delete_dir_contents',
            ))

        # %LOCALAPPDATA%\Temp
        local_temp = os.path.join(localapp, 'Temp')
        if os.path.isdir(local_temp) and os.path.normpath(local_temp) != os.path.normpath(temp_dir):
            count = _dir_item_count(local_temp)
            size  = _dir_size(local_temp)
            if size > 0:
                items.append(JunkItem(
                    category='logs',
                    label=f'LocalAppData Temp  ({count} items)',
                    path=local_temp,
                    size=size,
                    description='%LOCALAPPDATA%\\Temp — additional temporary / log files.',
                    exists=count > 0,
                    deletable=count > 0,
                    method='delete_dir_contents',
                ))

    elif sys.platform == 'darwin':
        home = os.path.expanduser('~')
        log_paths = [
            (os.path.join(home, 'Library', 'Logs'),
             'Application Logs',
             'macOS user application log files.'),
        ]
        for path, label, desc in log_paths:
            if not os.path.isdir(path):
                continue
            count = _dir_file_count_recursive(path)
            size  = _dir_size(path)
            if size > 0:
                items.append(JunkItem(
                    category='logs',
                    label=f'{label}  ({count} files)',
                    path=path,
                    size=size,
                    description=desc,
                    exists=count > 0,
                    deletable=count > 0,
                    method='delete_dir_contents',
                ))

    else:
        # Linux: look for large individual log files in /var/log
        var_log = '/var/log'
        if os.path.isdir(var_log):
            try:
                for entry in sorted(os.scandir(var_log), key=lambda e: e.name.lower()):
                    if entry.is_file(follow_symlinks=False):
                        try:
                            sz = entry.stat().st_size
                            if sz > 1_048_576:  # only show files > 1 MB
                                items.append(JunkItem(
                                    category='logs',
                                    label=f'{entry.name}  ({fmt_size(sz)})',
                                    path=entry.path,
                                    size=sz,
                                    description=f'Large log file: {entry.path}',
                                    exists=True,
                                    deletable=False,  # may need root
                                    method='delete',
                                ))
                        except OSError:
                            pass
            except (OSError, PermissionError):
                pass

    return items


# ── Scan all ──────────────────────────────────────────────────────────────────

def scan_all_junk() -> list[JunkItem]:
    results: list[JunkItem] = []
    for fn in (scan_temp_files, scan_windows_junk, scan_browser_caches,
               scan_crash_dumps, scan_log_files):
        try:
            results.extend(fn())
        except Exception:
            pass
    return results


# ── Clean ─────────────────────────────────────────────────────────────────────

def clean_junk_item(item: JunkItem, secure: bool = False) -> tuple[bool, str]:
    if not item.deletable:
        return False, 'Cannot be cleaned automatically'
    if not item.exists:
        return False, 'Item does not exist'
    try:
        if item.method == 'delete_dir_contents':
            # Delete everything inside the directory but keep the directory itself
            if not os.path.isdir(item.path):
                return False, f'Directory not found: {item.path}'
            errors: list[str] = []
            for entry in os.scandir(item.path):
                try:
                    if entry.is_file(follow_symlinks=False) or entry.is_symlink():
                        os.remove(entry.path)
                    elif entry.is_dir(follow_symlinks=False):
                        shutil.rmtree(entry.path, ignore_errors=True)
                except OSError as e:
                    errors.append(str(e))
            if errors:
                return True, f'Partial clean — {len(errors)} item(s) could not be removed'
            return True, ''

        elif item.method == 'delete_dir':
            if not os.path.isdir(item.path):
                return False, f'Directory not found: {item.path}'
            shutil.rmtree(item.path, ignore_errors=True)
            return True, ''

        elif item.method == 'delete':
            if not os.path.isfile(item.path):
                return False, f'File not found: {item.path}'
            os.remove(item.path)
            return True, ''

        elif item.method == 'command':
            if not item.command:
                return False, 'No command specified'
            result = subprocess.run(
                item.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                return True, ''
            return False, result.stderr.strip() or f'Command exited with code {result.returncode}'

        return False, f'Unknown method: {item.method}'

    except Exception as e:
        return False, str(e)


# ── Preview ───────────────────────────────────────────────────────────────────

_PREVIEW_MAX_ENTRIES = 60


def get_junk_preview(item: JunkItem) -> str:
    if not item.exists:
        return f'[Not found]\n\n{item.path}'

    if os.path.isdir(item.path):
        return _preview_junk_directory(item.path)

    if os.path.isfile(item.path):
        return _preview_junk_file(item.path)

    return f'[Path not accessible]\n\nPath: {item.path}'


def _preview_junk_directory(path: str) -> str:
    lines = [f'Directory: {path}', '']
    try:
        all_entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
        total_size = _dir_size(path)
        total_files = _dir_file_count_recursive(path)

        lines.append(
            f'{len(all_entries)} item(s) in dir   '
            f'{total_files} file(s) total   '
            f'{fmt_size(total_size)}'
        )
        lines.append('─' * 60)

        shown = all_entries[:_PREVIEW_MAX_ENTRIES]
        for e in shown:
            try:
                st = e.stat(follow_symlinks=False)
                mtime = datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')
                kind  = 'DIR ' if e.is_dir(follow_symlinks=False) else '    '
                size_str = fmt_size(st.st_size) if not e.is_dir(follow_symlinks=False) else '—'
                lines.append(f'  {kind} {size_str:>10}   {mtime}   {e.name}')
            except OSError:
                lines.append(f'  [unreadable]   {e.name}')

        if len(all_entries) > _PREVIEW_MAX_ENTRIES:
            lines.append(f'\n  … and {len(all_entries) - _PREVIEW_MAX_ENTRIES} more item(s)')

    except (OSError, PermissionError) as e:
        lines.append(f'[Permission denied: {e}]')
    return '\n'.join(lines)


def _preview_junk_file(path: str) -> str:
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return f'[Cannot stat file]\n\n{e}'

    lines = [f'File: {path}', f'Size: {fmt_size(size)}', '─' * 60, '']
    try:
        with open(path, 'rb') as f:
            raw = f.read(32768)
        if b'\x00' in raw[:256]:
            lines.append(f'[Binary file — {fmt_size(size)}]')
        else:
            text = raw.decode('utf-8', errors='replace')
            text_lines = text.splitlines()[:200]
            lines.extend(text_lines)
            if size > 32768:
                lines.append(f'\n… [truncated — showing first 32 KB of {fmt_size(size)}]')
    except (OSError, PermissionError) as e:
        lines.append(f'[Cannot read file: {e}]')
    return '\n'.join(lines)
