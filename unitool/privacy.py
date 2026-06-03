import os
import sys
import secrets
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ArtifactItem:
    category: str     # 'shell_history' | 'usb_traces' | 'credentials' | 'cloud' | 'network' | 'ram'
    label: str        # Display name shown in the table
    path: str         # File path, directory, or registry key
    size: int         # Bytes (0 for registry / non-file items)
    description: str  # Human-readable explanation
    exists: bool      # Whether the item actually exists on this system
    deletable: bool   # Whether it can be cleaned automatically
    method: str       # 'truncate'|'delete'|'delete_dir'|'clear_dir'|'registry'|'manual'


# ── Platform detection ────────────────────────────────────────────────────────

def get_platform_info() -> dict:
    info = {
        'system':   sys.platform,
        'name':     '',
        'version':  '',
        'distro':   '',
        'arch':     platform.machine(),
        'is_admin': _is_admin(),
    }
    if sys.platform == 'win32':
        info['name']    = 'Windows'
        info['version'] = platform.version()
    elif sys.platform == 'darwin':
        info['name']    = 'macOS'
        info['version'] = platform.mac_ver()[0]
    else:
        info['name']    = 'Linux'
        info['version'] = platform.release()
        info['distro']  = _get_linux_distro()
    return info


def _is_admin() -> bool:
    try:
        if sys.platform == 'win32':
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


def _get_linux_distro() -> str:
    try:
        data: dict[str, str] = {}
        with open('/etc/os-release') as f:
            for line in f:
                line = line.strip()
                if '=' in line:
                    k, v = line.split('=', 1)
                    data[k] = v.strip('"')
        return data.get('PRETTY_NAME', data.get('NAME', ''))
    except Exception:
        return ''


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _file_item(category: str, label: str, path: str,
               description: str, method: str = 'delete') -> ArtifactItem:
    exists = os.path.isfile(path)
    return ArtifactItem(
        category=category, label=label, path=path,
        size=os.path.getsize(path) if exists else 0,
        description=description, exists=exists,
        deletable=exists, method=method,
    )


def fmt_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n:.1f} {unit}' if unit != 'B' else f'{n} B'
        n /= 1024
    return f'{n:.1f} PB'


# ── Shell history ─────────────────────────────────────────────────────────────

def scan_shell_history() -> list[ArtifactItem]:
    items: list[ArtifactItem] = []
    home = os.path.expanduser('~')
    if sys.platform == 'win32':
        ps_path = os.path.join(
            os.environ.get('APPDATA', home),
            'Microsoft', 'Windows', 'PowerShell', 'PSReadLine',
            'ConsoleHost_history.txt',
        )
        items.append(_file_item('shell_history', 'PowerShell History', ps_path,
                                'PowerShell command history', 'truncate'))
    else:
        for fname, label in [('.bash_history', 'Bash History'),
                              ('.zsh_history', 'Zsh History')]:
            items.append(_file_item('shell_history', label,
                                    os.path.join(home, fname),
                                    f'~/{fname}', 'truncate'))
        fish = os.path.join(home, '.local', 'share', 'fish', 'fish_history')
        if os.path.exists(fish):
            items.append(_file_item('shell_history', 'Fish History', fish,
                                    'Fish shell history', 'truncate'))
    return items


# ── USB traces ────────────────────────────────────────────────────────────────

def scan_usb_traces() -> list[ArtifactItem]:
    items: list[ArtifactItem] = []
    if sys.platform == 'win32':
        items.append(ArtifactItem(
            category='usb_traces',
            label='USBSTOR Registry Key',
            path=r'HKLM\SYSTEM\CurrentControlSet\Enum\USBSTOR',
            size=0,
            description='USB device connection history stored in Windows Registry. '
                        'Contains all USB storage devices ever connected.',
            exists=True, deletable=False, method='registry',
        ))
        log = os.path.join(
            os.environ.get('WINDIR', r'C:\Windows'), 'INF', 'setupapi.dev.log',
        )
        items.append(_file_item('usb_traces', 'SetupAPI Dev Log', log,
                                'Windows device installation log — records every device ever plugged in'))
    elif sys.platform == 'darwin':
        items.append(_file_item('usb_traces', 'System Log',
                                '/var/log/system.log',
                                'macOS system log (contains USB mount/unmount events)'))
        wifi_log = os.path.expanduser('~/Library/Logs/WiFiManager')
        if os.path.isdir(wifi_log):
            items.append(ArtifactItem(
                category='usb_traces', label='WiFi Manager Logs',
                path=wifi_log, size=_dir_size(wifi_log),
                description='macOS WiFi connection logs',
                exists=True, deletable=True, method='delete_dir',
            ))
    else:
        for path, label, desc in [
            ('/var/log/syslog',   'syslog',    'System log — contains USB mount events'),
            ('/var/log/kern.log', 'kern.log',  'Kernel log'),
            ('/var/log/dmesg',    'dmesg log', 'Kernel ring buffer log'),
        ]:
            if os.path.exists(path):
                items.append(_file_item('usb_traces', label, path, desc))
    return items


# ── Network / forensics traces ────────────────────────────────────────────────

def scan_network_traces() -> list[ArtifactItem]:
    items: list[ArtifactItem] = []
    home = os.path.expanduser('~')

    if sys.platform == 'win32':
        # Recent files (Shell LNK files)
        recent = os.path.join(
            os.environ.get('APPDATA', home), 'Microsoft', 'Windows', 'Recent',
        )
        if os.path.isdir(recent):
            lnk_files = [f for f in os.listdir(recent) if f.lower().endswith('.lnk')]
            lnk_size = sum(
                os.path.getsize(os.path.join(recent, f))
                for f in lnk_files
                if os.path.isfile(os.path.join(recent, f))
            )
            items.append(ArtifactItem(
                category='network',
                label=f'Recent Files  ({len(lnk_files)} shortcuts)',
                path=recent, size=lnk_size,
                description='Windows Shell recently opened files. '
                            'Each .lnk shortcut reveals the full path, volume serial number, '
                            'MAC address of the machine where the file was last opened, '
                            'and timestamps.',
                exists=bool(lnk_files), deletable=bool(lnk_files), method='clear_dir',
            ))

        # Jump Lists (auto-populated per-app recent file lists)
        auto_dest = os.path.join(recent, 'AutomaticDestinations')
        cust_dest = os.path.join(recent, 'CustomDestinations')
        for jl_path, jl_label in [
            (auto_dest, 'Jump Lists — Automatic'),
            (cust_dest, 'Jump Lists — Custom'),
        ]:
            if os.path.isdir(jl_path):
                jl_size = _dir_size(jl_path)
                count = len(os.listdir(jl_path))
                if count:
                    items.append(ArtifactItem(
                        category='network', label=f'{jl_label}  ({count} files)',
                        path=jl_path, size=jl_size,
                        description='Per-application recently used file lists shown in taskbar context menus. '
                                    'Contain embedded LNK records with path and MAC address metadata.',
                        exists=True, deletable=True, method='delete_dir',
                    ))

        # Prefetch
        prefetch = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Prefetch')
        if os.path.isdir(prefetch):
            try:
                pf_files = [f for f in os.listdir(prefetch) if f.lower().endswith('.pf')]
            except PermissionError:
                pf_files = []
            items.append(ArtifactItem(
                category='network',
                label=f'Prefetch Cache  ({len(pf_files)} programs)',
                path=prefetch, size=_dir_size(prefetch),
                description='Windows execution artefacts — one .pf file per program ever run. '
                            'Contains program path, run count, last run timestamp, and loaded DLL list.',
                exists=bool(pf_files), deletable=False, method='manual',
            ))

        # Thumbnail cache
        exp_dir = os.path.join(
            os.environ.get('LOCALAPPDATA', home),
            'Microsoft', 'Windows', 'Explorer',
        )
        if os.path.isdir(exp_dir):
            thumb_files = [
                os.path.join(exp_dir, f)
                for f in os.listdir(exp_dir)
                if f.lower().startswith('thumbcache') and f.lower().endswith('.db')
            ]
            if thumb_files:
                thumb_size = sum(
                    os.path.getsize(p) for p in thumb_files if os.path.isfile(p)
                )
                items.append(ArtifactItem(
                    category='network',
                    label=f'Thumbnail Cache  ({len(thumb_files)} databases)',
                    path=exp_dir, size=thumb_size,
                    description='Windows Explorer thumbnail image cache. '
                                'Reveals recently viewed images even after originals are deleted. '
                                'Files are often locked by Explorer.',
                    exists=True, deletable=False, method='manual',
                ))

        # IE/Edge/WebCache
        inet_cache = os.path.join(
            os.environ.get('LOCALAPPDATA', home), 'Microsoft', 'Windows', 'INetCache',
        )
        if os.path.isdir(inet_cache):
            items.append(ArtifactItem(
                category='network', label='Internet Cache (INetCache)',
                path=inet_cache, size=_dir_size(inet_cache),
                description='Internet Explorer / WebView2 browser cache and temporary internet files.',
                exists=True, deletable=True, method='delete_dir',
            ))

    elif sys.platform == 'darwin':
        shared_fl = os.path.join(
            home, 'Library', 'Application Support', 'com.apple.sharedfilelist',
        )
        if os.path.isdir(shared_fl):
            items.append(ArtifactItem(
                category='network', label='Recent Files (sharedfilelist)',
                path=shared_fl, size=_dir_size(shared_fl),
                description='macOS recent files and application access history.',
                exists=True, deletable=False, method='manual',
            ))
        quarantine_db = os.path.join(
            home, 'Library', 'Preferences',
            'com.apple.LaunchServices.QuarantineEventsV2',
        )
        if os.path.exists(quarantine_db):
            items.append(_file_item(
                'network', 'Quarantine Events DB', quarantine_db,
                'macOS GateKeeper quarantine log — records every downloaded file ever opened.',
            ))
    else:
        # GTK recently used files
        recent_xbel = os.path.join(home, '.local', 'share', 'recently-used.xbel')
        if os.path.exists(recent_xbel):
            items.append(_file_item(
                'network', 'GTK Recent Files', recent_xbel,
                'GTK+ recently used files list (GNOME Files, gedit, etc.)',
                'truncate',
            ))
        # Login history
        for path, label, desc in [
            ('/var/log/auth.log', 'Auth Log', 'SSH / sudo authentication log'),
            ('/var/log/wtmp', 'Login History (wtmp)', 'Binary file — all login/logout records'),
            ('/var/log/btmp', 'Failed Logins (btmp)', 'Binary file — failed login attempts'),
        ]:
            if os.path.exists(path):
                items.append(_file_item('network', label, path, desc))

    return items


# ── Credentials ───────────────────────────────────────────────────────────────

def scan_credentials() -> list[ArtifactItem]:
    items: list[ArtifactItem] = []
    home = os.path.expanduser('~')
    if sys.platform == 'win32':
        cred_dir = os.path.join(
            os.environ.get('LOCALAPPDATA', home), 'Microsoft', 'Credentials',
        )
        if os.path.isdir(cred_dir):
            for fname in os.listdir(cred_dir):
                fpath = os.path.join(cred_dir, fname)
                if os.path.isfile(fpath):
                    items.append(_file_item(
                        'credentials', f'Credential: {fname[:20]}', fpath,
                        'Windows Credential Manager stored credential (encrypted DPAPI blob)',
                    ))
        vault_dir = os.path.join(
            os.environ.get('LOCALAPPDATA', home), 'Microsoft', 'Vault',
        )
        if os.path.isdir(vault_dir):
            items.append(ArtifactItem(
                category='credentials', label='Windows Vault',
                path=vault_dir, size=_dir_size(vault_dir),
                description='Windows Credential Vault — manage via Control Panel > Credential Manager',
                exists=True, deletable=False, method='manual',
            ))
    elif sys.platform == 'darwin':
        kc_dir = os.path.join(home, 'Library', 'Keychains')
        items.append(ArtifactItem(
            category='credentials', label='macOS Keychain',
            path=kc_dir,
            size=_dir_size(kc_dir) if os.path.isdir(kc_dir) else 0,
            description='macOS Keychain — manage via Keychain Access app',
            exists=os.path.isdir(kc_dir), deletable=False, method='manual',
        ))
    else:
        gnome_kr = os.path.join(home, '.local', 'share', 'keyrings')
        if os.path.isdir(gnome_kr):
            items.append(ArtifactItem(
                category='credentials', label='GNOME Keyring',
                path=gnome_kr, size=_dir_size(gnome_kr),
                description='GNOME Keyring credential store',
                exists=True, deletable=False, method='manual',
            ))
        kde_wallet = os.path.join(home, '.local', 'share', 'kwalletd')
        if os.path.isdir(kde_wallet):
            items.append(ArtifactItem(
                category='credentials', label='KDE Wallet',
                path=kde_wallet, size=_dir_size(kde_wallet),
                description='KDE Wallet credential store',
                exists=True, deletable=False, method='manual',
            ))
    return items


# ── Clipboard ─────────────────────────────────────────────────────────────────

def scan_clipboard() -> list[ArtifactItem]:
    """
    Returns file-based clipboard artifacts (history DBs).
    Current in-memory clipboard contents are handled in the UI thread via Qt.
    """
    items: list[ArtifactItem] = []
    home = os.path.expanduser('~')

    if sys.platform == 'win32':
        local_app = os.environ.get('LOCALAPPDATA', home)

        # Windows 10/11 clipboard history stored inside a UWP package
        packages_dir = os.path.join(local_app, 'Packages')
        if os.path.isdir(packages_dir):
            try:
                for pkg in os.listdir(packages_dir):
                    if 'CBS' in pkg:
                        cb_data = os.path.join(
                            packages_dir, pkg, 'LocalState', 'ClipboardData',
                        )
                        if os.path.isdir(cb_data):
                            items.append(ArtifactItem(
                                category='clipboard',
                                label='Clipboard History Database',
                                path=cb_data,
                                size=_dir_size(cb_data),
                                description=(
                                    'Windows 10/11 clipboard history — up to 25 recent clipboard items '
                                    'stored as encrypted DPAPI blobs in a UWP package directory.'
                                ),
                                exists=True, deletable=True, method='delete_dir',
                            ))
            except PermissionError:
                pass

        # Legacy clipboard cache folder (some Windows builds)
        legacy = os.path.join(local_app, 'Microsoft', 'Windows', 'Clipboard')
        if os.path.isdir(legacy):
            items.append(ArtifactItem(
                category='clipboard',
                label='Clipboard Cache',
                path=legacy,
                size=_dir_size(legacy),
                description='Windows clipboard cache directory.',
                exists=True, deletable=True, method='delete_dir',
            ))

    elif sys.platform == 'darwin':
        # macOS clipboard is in-memory only; no persistent history file by default
        # GPaste / Clipy and other managers store their DBs in Application Support
        for app_dir, label, desc in [
            ('net.uliwitness.Clipy',   'Clipy History',   'Clipy clipboard manager database'),
            ('com.utsire.yippy',       'Yippy History',   'Yippy clipboard manager history'),
            ('org.gnome.GPaste',       'GPaste History',  'GPaste clipboard manager history'),
        ]:
            p = os.path.join(home, 'Library', 'Application Support', app_dir)
            if os.path.isdir(p):
                items.append(ArtifactItem(
                    category='clipboard', label=label, path=p,
                    size=_dir_size(p), description=desc,
                    exists=True, deletable=True, method='delete_dir',
                ))

    else:
        # Linux clipboard managers
        for rel_path, label, desc in [
            ('.local/share/GPaste',           'GPaste History',   'GPaste clipboard manager'),
            ('.local/share/parcellite',       'Parcellite',       'Parcellite clipboard manager'),
            ('.local/share/clipman',          'Clipman',          'Xfce Clipman history'),
            ('.config/copyq',                 'CopyQ',            'CopyQ clipboard manager'),
        ]:
            p = os.path.join(home, rel_path)
            if os.path.isdir(p):
                items.append(ArtifactItem(
                    category='clipboard', label=label, path=p,
                    size=_dir_size(p), description=desc,
                    exists=True, deletable=True, method='delete_dir',
                ))

    return items


# ── Cloud sync traces ─────────────────────────────────────────────────────────

def scan_cloud_traces() -> list[ArtifactItem]:
    items: list[ArtifactItem] = []
    home = os.path.expanduser('~')
    if sys.platform == 'win32':
        od_base = os.path.join(
            os.environ.get('LOCALAPPDATA', home), 'Microsoft', 'OneDrive',
        )
        for sub, label, desc in [
            ('logs',  'OneDrive Logs',        'Microsoft OneDrive diagnostic logs'),
            ('setup', 'OneDrive Setup Cache', 'OneDrive setup cache files'),
        ]:
            path = os.path.join(od_base, sub)
            if os.path.isdir(path):
                items.append(ArtifactItem(
                    category='cloud', label=label, path=path,
                    size=_dir_size(path), description=desc,
                    exists=True, deletable=True, method='delete_dir',
                ))
    elif sys.platform == 'darwin':
        icloud_logs = os.path.join(home, 'Library', 'Logs', 'CloudDocs')
        if os.path.isdir(icloud_logs):
            items.append(ArtifactItem(
                category='cloud', label='iCloud Logs',
                path=icloud_logs, size=_dir_size(icloud_logs),
                description='iCloud Drive diagnostic logs',
                exists=True, deletable=True, method='delete_dir',
            ))
        clouddocs = os.path.join(home, 'Library', 'Application Support', 'CloudDocs')
        if os.path.isdir(clouddocs):
            items.append(ArtifactItem(
                category='cloud', label='iCloud CloudDocs DB',
                path=clouddocs, size=_dir_size(clouddocs),
                description='iCloud Drive local metadata database (system-managed)',
                exists=True, deletable=False, method='manual',
            ))
    return items


# ── RAM info + clean ──────────────────────────────────────────────────────────

def get_ram_info() -> dict:
    """Returns {'total', 'used', 'available', 'percent'} in bytes / percent."""
    try:
        if sys.platform == 'win32':
            return _ram_info_windows()
        elif sys.platform == 'darwin':
            return _ram_info_macos()
        else:
            return _ram_info_linux()
    except Exception:
        return {'total': 0, 'used': 0, 'available': 0, 'percent': 0}


def _ram_info_windows() -> dict:
    import ctypes
    import ctypes.wintypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ('dwLength',                ctypes.c_ulong),
            ('dwMemoryLoad',            ctypes.c_ulong),
            ('ullTotalPhys',            ctypes.c_ulonglong),
            ('ullAvailPhys',            ctypes.c_ulonglong),
            ('ullTotalPageFile',        ctypes.c_ulonglong),
            ('ullAvailPageFile',        ctypes.c_ulonglong),
            ('ullTotalVirtual',         ctypes.c_ulonglong),
            ('ullAvailVirtual',         ctypes.c_ulonglong),
            ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(stat)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    return {
        'total':     stat.ullTotalPhys,
        'used':      stat.ullTotalPhys - stat.ullAvailPhys,
        'available': stat.ullAvailPhys,
        'percent':   stat.dwMemoryLoad,
    }


def _ram_info_linux() -> dict:
    data: dict[str, int] = {}
    with open('/proc/meminfo') as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                data[parts[0].rstrip(':')] = int(parts[1]) * 1024
    total     = data.get('MemTotal', 0)
    available = data.get('MemAvailable', data.get('MemFree', 0))
    used      = total - available
    return {
        'total': total, 'used': used, 'available': available,
        'percent': int(used / total * 100) if total else 0,
    }


def _ram_info_macos() -> dict:
    page_size = 4096
    try:
        r = subprocess.run(['sysctl', '-n', 'hw.pagesize'],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            page_size = int(r.stdout.strip())
    except Exception:
        pass

    total = 0
    try:
        r = subprocess.run(['sysctl', '-n', 'hw.memsize'],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            total = int(r.stdout.strip())
    except Exception:
        pass

    pages: dict[str, int] = {}
    try:
        r = subprocess.run(['vm_stat'], capture_output=True, text=True, timeout=3)
        for line in r.stdout.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                try:
                    pages[k.strip()] = int(v.strip().rstrip('.'))
                except ValueError:
                    pass
    except Exception:
        pass

    free = (pages.get('Pages free', 0) + pages.get('Pages inactive', 0)) * page_size
    used = total - free
    return {
        'total': total, 'used': used, 'available': free,
        'percent': int(used / total * 100) if total else 0,
    }


def clean_ram() -> tuple[int, str]:
    """
    Trim process working sets to release pages to the standby list.
    Returns (bytes_freed, error_message).
    Note: on SSDs freed bytes may not reflect physical DRAM release immediately.
    """
    try:
        before = get_ram_info()['available']
        if sys.platform == 'win32':
            _clean_ram_windows()
        elif sys.platform == 'darwin':
            subprocess.run(['sudo', 'purge'], capture_output=True, timeout=15)
        else:
            if _is_admin():
                with open('/proc/sys/vm/drop_caches', 'w') as f:
                    f.write('3')
        after = get_ram_info()['available']
        return max(0, after - before), ''
    except Exception as e:
        return 0, str(e)


def _clean_ram_windows():
    import ctypes
    import ctypes.wintypes

    # Try to flush modified + standby lists (works with admin; silently ignored without)
    ntdll = ctypes.windll.ntdll
    for cmd_val in (3, 4):  # MemoryFlushModifiedList=3, MemoryPurgeStandbyList=4
        cmd = ctypes.c_int(cmd_val)
        ntdll.NtSetSystemInformation(80, ctypes.byref(cmd), ctypes.sizeof(cmd))

    # Trim working set of every accessible process
    PROCESS_SET_QUOTA          = 0x0100
    PROCESS_QUERY_INFORMATION  = 0x0400
    TH32CS_SNAPPROCESS         = 0x00000002
    INVALID_HANDLE_VALUE       = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ('dwSize',              ctypes.c_ulong),
            ('cntUsage',            ctypes.c_ulong),
            ('th32ProcessID',       ctypes.c_ulong),
            ('th32DefaultHeapID',   ctypes.POINTER(ctypes.c_ulong)),
            ('th32ModuleID',        ctypes.c_ulong),
            ('cntThreads',          ctypes.c_ulong),
            ('th32ParentProcessID', ctypes.c_ulong),
            ('pcPriClassBase',      ctypes.c_long),
            ('dwFlags',             ctypes.c_ulong),
            ('szExeFile',           ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return

    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(entry)
    if kernel32.Process32First(snap, ctypes.byref(entry)):
        while True:
            h = kernel32.OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False,
                entry.th32ProcessID,
            )
            if h:
                kernel32.SetProcessWorkingSetSize(
                    h, ctypes.c_size_t(-1), ctypes.c_size_t(-1),
                )
                kernel32.CloseHandle(h)
            if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                break
    kernel32.CloseHandle(snap)


# ── Scan all ──────────────────────────────────────────────────────────────────

def scan_all() -> list[ArtifactItem]:
    results: list[ArtifactItem] = []
    for fn in (scan_shell_history, scan_usb_traces, scan_network_traces,
               scan_credentials, scan_cloud_traces, scan_clipboard):
        try:
            results.extend(fn())
        except Exception:
            pass
    return results


# ── Clean ─────────────────────────────────────────────────────────────────────

def clean_item(item: ArtifactItem, secure: bool = False) -> tuple[bool, str]:
    if not item.deletable:
        return False, 'Cannot be cleaned automatically'
    try:
        if item.method == 'truncate':
            with open(item.path, 'w'):
                pass
            return True, ''
        elif item.method == 'delete':
            if secure:
                return _secure_delete_file(item.path)
            os.remove(item.path)
            return True, ''
        elif item.method == 'delete_dir':
            import shutil
            shutil.rmtree(item.path, ignore_errors=True)
            return True, ''
        elif item.method == 'clear_dir':
            # Delete contents but preserve the directory itself
            import shutil
            for entry in os.scandir(item.path):
                try:
                    if entry.is_file(follow_symlinks=False):
                        os.remove(entry.path)
                    elif entry.is_dir(follow_symlinks=False):
                        shutil.rmtree(entry.path, ignore_errors=True)
                except OSError:
                    pass
            return True, ''
        return False, f'Unknown method: {item.method}'
    except Exception as e:
        return False, str(e)


def _secure_delete_file(path: str) -> tuple[bool, str]:
    """
    Platform-aware secure delete.
    SSD note: overwrite is largely ineffective due to wear levelling.
    TRIM / blkdiscard is the correct approach for SSDs.
    """
    try:
        if sys.platform == 'win32':
            _overwrite_file(path)
            os.remove(path)
        elif sys.platform == 'darwin':
            r = subprocess.run(['rm', '-P', path], capture_output=True, timeout=30)
            if r.returncode != 0:
                _overwrite_file(path)
                os.remove(path)
        else:
            r = subprocess.run(
                ['shred', '-u', '-n', '3', '-z', path],
                capture_output=True, timeout=60,
            )
            if r.returncode != 0:
                _overwrite_file(path)
                os.remove(path)
        return True, ''
    except Exception as e:
        return False, str(e)


def _overwrite_file(path: str):
    size = os.path.getsize(path)
    if size == 0:
        return
    with open(path, 'r+b') as f:
        for _ in range(3):
            f.seek(0)
            f.write(secrets.token_bytes(size))
            f.flush()
            os.fsync(f.fileno())


# ── Preview ───────────────────────────────────────────────────────────────────

_PREVIEW_MAX_BYTES = 32_768   # 32 KB read limit for text preview
_PREVIEW_MAX_LINES = 200


def get_artifact_preview(item: ArtifactItem) -> str:
    """
    Return a text preview of the artifact for display in the UI.
    Called on demand (not during scan) so it can do extra I/O.
    """
    if not item.exists:
        return f'[Not found]\n\n{item.path}'

    # Current clipboard items have a special virtual path; their description
    # already contains the preview text set by the UI before spawning the worker.
    if item.path.startswith('<clipboard:'):
        return item.description

    if item.method == 'registry':
        return (
            f'Registry Key\n{"─"*50}\n'
            f'{item.path}\n\n'
            f'{item.description}\n\n'
            f'How to view:\n'
            f'  1. Press Win + R, type regedit, press Enter\n'
            f'  2. Navigate to the key above\n\n'
            f'How to remove (requires Administrator):\n'
            f'  Right-click the key → Delete'
        )

    if os.path.isdir(item.path):
        return _preview_directory(item.path)

    if os.path.isfile(item.path):
        return _preview_file(item)

    return f'[Artifact not readable]\n\nPath: {item.path}'


def _preview_directory(path: str) -> str:
    lines = [f'Directory: {path}', '']
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
        lines.append(f'{len(entries)} item(s)  —  {fmt_size(_dir_size(path))} total')
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


def _preview_file(item: ArtifactItem) -> str:
    path, size = item.path, item.size

    # Credential / binary blobs: never display contents
    if item.category == 'credentials':
        return (
            f'Credential file  —  {fmt_size(size)}\n'
            f'{"─"*50}\n'
            f'{path}\n\n'
            f'{item.description}\n\n'
            f'[Contents not shown — file contains encrypted credential data]'
        )

    try:
        # Read a chunk to detect binary
        with open(path, 'rb') as f:
            raw = f.read(_PREVIEW_MAX_BYTES)

        if b'\x00' in raw[:256]:
            return f'[Binary file — {fmt_size(size)}]\n\nPath: {path}'

        text = raw.decode('utf-8', errors='replace')
        all_lines = text.splitlines()

        is_partial = size > _PREVIEW_MAX_BYTES
        total_lines_approx = len(all_lines)

        # For history/command files: tail the file (show most recent commands)
        if item.method == 'truncate' and size <= 2_097_152:  # ≤ 2 MB: full read
            try:
                with open(path, 'r', errors='replace') as f:
                    full_lines = f.readlines()
                shown   = full_lines[-_PREVIEW_MAX_LINES:]
                omitted = len(full_lines) - len(shown)
                header  = (
                    f'{path}\n'
                    f'{"─"*60}\n'
                    f'{len(full_lines)} command(s) total'
                    + (f'  —  showing last {len(shown)}' if omitted else '')
                    + '\n\n'
                )
                return header + ''.join(shown)
            except OSError:
                pass  # fall through to chunk preview

        prefix = (
            f'{path}\n'
            f'{"─"*60}\n'
            + (f'[Partial: first {fmt_size(_PREVIEW_MAX_BYTES)} of {fmt_size(size)}]\n\n'
               if is_partial else '')
        )
        return prefix + '\n'.join(all_lines[:_PREVIEW_MAX_LINES])

    except (OSError, PermissionError) as e:
        return f'[Cannot read file]\n\n{e}'
