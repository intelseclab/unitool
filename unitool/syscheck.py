"""
UniTool/syscheck.py
Backend for the System Check tab — ComboFix-inspired security scanner.
"""
from __future__ import annotations

import os
import sys
import ctypes
import platform
import subprocess
import json
import datetime
from dataclasses import dataclass, field
from typing import Callable

# ── Windows-only imports & ctypes helpers ───────────────────────────────────
if sys.platform == 'win32':
    import winreg  # noqa: F401  (used throughout the module)
    import ctypes.wintypes as _wt

    _k32   = ctypes.WinDLL('kernel32', use_last_error=True)
    _psapi = ctypes.WinDLL('psapi',    use_last_error=True)

    _PROCESS_QUERY_LIMITED = 0x1000
    _TH32CS_SNAPPROCESS    = 0x00000002
    # INVALID_HANDLE_VALUE = (HANDLE)-1
    _INVALID_HANDLE = ctypes.c_size_t(-1).value

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ('dwSize',              ctypes.c_uint32),
            ('cntUsage',            ctypes.c_uint32),
            ('th32ProcessID',       ctypes.c_uint32),
            ('th32DefaultHeapID',   ctypes.c_size_t),   # ULONG_PTR
            ('th32ModuleID',        ctypes.c_uint32),
            ('cntThreads',          ctypes.c_uint32),
            ('th32ParentProcessID', ctypes.c_uint32),
            ('pcPriClassBase',      ctypes.c_int32),
            ('dwFlags',             ctypes.c_uint32),
            ('szExeFile',           ctypes.c_wchar * 260),
        ]

    _k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    _k32.OpenProcess.restype              = ctypes.c_void_p
    _k32.CloseHandle.restype              = ctypes.c_bool
    _k32.QueryFullProcessImageNameW.restype = ctypes.c_bool

    def _win_enum_pids_psapi() -> set[int]:
        arr    = (_wt.DWORD * 4096)()
        needed = _wt.DWORD(0)
        if not _psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
            return set()
        count = needed.value // ctypes.sizeof(_wt.DWORD)
        return {arr[i] for i in range(count) if arr[i]}

    def _win_enum_pids_toolhelp() -> dict[int, str]:
        snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snap is None or snap in (0, _INVALID_HANDLE):
            return {}
        result: dict[int, str] = {}
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        try:
            if _k32.Process32FirstW(snap, ctypes.byref(entry)):
                result[entry.th32ProcessID] = entry.szExeFile
                while _k32.Process32NextW(snap, ctypes.byref(entry)):
                    result[entry.th32ProcessID] = entry.szExeFile
        finally:
            _k32.CloseHandle(snap)
        return result

    def _win_pid_walk(known: set[int],
                      max_hidden: int = 40) -> list[tuple[int, str, str]]:
        """Walk PIDs 4..65532 step 4; return (pid, name, path) for those
        that can be opened but are absent from *known*."""
        found: list[tuple[int, str, str]] = []
        for pid in range(4, 65536, 4):
            if pid in known:
                continue
            h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED, False, pid)
            if not h or h == _INVALID_HANDLE:
                continue
            buf  = ctypes.create_unicode_buffer(512)
            size = _wt.DWORD(512)
            _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            _k32.CloseHandle(h)
            full = buf.value or ''
            name = os.path.basename(full) if full else f'PID {pid}'
            found.append((pid, name, full))
            if len(found) >= max_hidden:
                break
        return found

    def _win_enum_drivers() -> list[str]:
        """Return kernel-format path strings for all loaded drivers."""
        arr    = (ctypes.c_void_p * 4096)()
        needed = _wt.DWORD(0)
        if not _psapi.EnumDeviceDrivers(arr, ctypes.sizeof(arr),
                                        ctypes.byref(needed)):
            return []
        count = needed.value // ctypes.sizeof(ctypes.c_void_p)
        paths: list[str] = []
        for i in range(count):
            if arr[i] is None:
                continue
            buf = ctypes.create_unicode_buffer(512)
            _psapi.GetDeviceDriverFileNameW(arr[i], buf, 512)
            if buf.value:
                paths.append(buf.value)
        return paths

    def _kernel_path_to_real(kpath: str) -> str:
        r"""Convert \SystemRoot\... / \??\C:\... to a filesystem path."""
        sys_root = os.environ.get('SystemRoot', r'C:\Windows')
        p = kpath
        for prefix in ('\\SystemRoot\\', '\\Windows\\'):
            if p.lower().startswith(prefix.lower()):
                return sys_root + '\\' + p[len(prefix):]
        if p.lower().startswith('\\??\\'):
            return p[4:]
        return p


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    category: str
    title: str
    detail: str
    location: str
    value: str
    risk: str          # 'high' | 'medium' | 'low' | 'info'
    fixable: bool
    fix_method: str    # 'delete_reg_value' | 'delete_reg_key' | 'delete_file'
                       # | 'restore_reg_value' | 'manual'
    fix_params: dict = field(default_factory=dict)
    fixed: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Category registry
# ─────────────────────────────────────────────────────────────────────────────

CATEGORIES = [
    ('startup',    '⚡', 'sc_cat_startup'),
    ('integrity',  '🔐', 'sc_cat_integrity'),
    ('hosts',      '🌐', 'sc_cat_hosts'),
    ('processes',  '⚙',  'sc_cat_processes'),
    ('tasks',      '📅', 'sc_cat_tasks'),
    ('network',    '🔌', 'sc_cat_network'),
    ('indicators', '🚨', 'sc_cat_indicators'),
    ('browser',    '🌍', 'sc_cat_browser'),
    ('rootkit',    '🦠', 'sc_cat_rootkit'),
]

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _suspicious_dirs() -> list[str]:
    """Return lowercased real paths that count as suspicious locations."""
    paths: list[str] = []
    for var in ('TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA'):
        v = os.environ.get(var, '')
        if v:
            paths.append(os.path.normcase(os.path.realpath(v)))
    # user Downloads folder
    home = os.path.expanduser('~')
    downloads = os.path.join(home, 'Downloads')
    paths.append(os.path.normcase(os.path.realpath(downloads)))
    return paths


def _under_suspicious(path: str, dirs: list[str]) -> bool:
    """Return True if *path* is located inside any of the suspicious dirs."""
    norm = os.path.normcase(os.path.realpath(path))
    return any(norm.startswith(d + os.sep) or norm == d for d in dirs)


def _under_temp_only(path: str) -> bool:
    """Return True only for TEMP/TMP/LocalAppData\\Temp paths (not Downloads)."""
    norm = os.path.normcase(os.path.realpath(path))
    for var in ('TEMP', 'TMP'):
        v = os.environ.get(var, '')
        if v:
            nd = os.path.normcase(os.path.realpath(v))
            if norm.startswith(nd + os.sep) or norm == nd:
                return True
    # APPDATA\anything\Temp  and  LOCALAPPDATA\Temp
    appdata = os.environ.get('APPDATA', '')
    if appdata:
        nd = os.path.normcase(os.path.realpath(appdata))
        if norm.startswith(nd + os.sep):
            rest = norm[len(nd) + 1:]
            parts = rest.split(os.sep)
            if len(parts) >= 2 and parts[1].lower() == 'temp':
                return True
    localappdata = os.environ.get('LOCALAPPDATA', '')
    if localappdata:
        nd = os.path.normcase(os.path.realpath(localappdata))
        temp_sub = os.path.join(nd, 'temp')
        if norm.startswith(temp_sub + os.sep) or norm == temp_sub:
            return True
    return False


def _is_downloads(path: str) -> bool:
    downloads = os.path.normcase(os.path.realpath(
        os.path.join(os.path.expanduser('~'), 'Downloads')
    ))
    norm = os.path.normcase(os.path.realpath(path))
    return norm.startswith(downloads + os.sep) or norm == downloads


def _hive_name(hive_int: int) -> str:
    if sys.platform != 'win32':
        return str(hive_int)
    _map = {
        winreg.HKEY_LOCAL_MACHINE: 'HKLM',
        winreg.HKEY_CURRENT_USER:  'HKCU',
        winreg.HKEY_CLASSES_ROOT:  'HKCR',
        winreg.HKEY_USERS:         'HKU',
    }
    return _map.get(hive_int, str(hive_int))


def _reg_open(hive: int, subkey: str, access: int = None):
    """Open a registry key; return None if it doesn't exist."""
    if sys.platform != 'win32':
        return None
    if access is None:
        access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    try:
        return winreg.OpenKey(hive, subkey, 0, access)
    except OSError:
        return None


def _reg_get(hive: int, subkey: str, value_name: str):
    """Return (data, type) for a registry value, or (None, None) on failure."""
    key = _reg_open(hive, subkey)
    if key is None:
        return None, None
    try:
        data, typ = winreg.QueryValueEx(key, value_name)
        return data, typ
    except OSError:
        return None, None
    finally:
        key.Close()


def _reg_delete_key_recursive(hive: int, subkey: str):
    """Recursively delete a registry key and all its sub-keys."""
    try:
        key = winreg.OpenKey(hive, subkey, 0,
                             winreg.KEY_READ | winreg.KEY_WOW64_64KEY |
                             winreg.KEY_WRITE)
    except OSError as exc:
        raise exc
    # Enumerate and delete children first
    children: list[str] = []
    idx = 0
    while True:
        try:
            children.append(winreg.EnumKey(key, idx))
            idx += 1
        except OSError:
            break
    key.Close()
    for child in children:
        child_path = subkey + '\\' + child
        _reg_delete_key_recursive(hive, child_path)
    winreg.DeleteKey(hive, subkey)


def _extract_exe_path(cmd: str) -> str:
    """Extract the executable path from a shell command string."""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end != -1:
            return cmd[1:end]
    return cmd.split()[0] if cmd else ''


def _parse_ps_json(raw: bytes) -> list[dict]:
    """Parse PowerShell ConvertTo-Json output — returns a list of dicts."""
    try:
        text = raw.decode('utf-8', errors='replace').strip()
        if not text:
            return []
        data = json.loads(text)
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (json.JSONDecodeError, ValueError):
        pass
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Scan functions
# ─────────────────────────────────────────────────────────────────────────────

def scan_startup_persistence() -> list[Finding]:
    """Scan registry Run / RunOnce keys for suspicious executable locations."""
    if sys.platform != 'win32':
        return []
    findings: list[Finding] = []
    susp_dirs = _suspicious_dirs()

    run_locations = [
        (winreg.HKEY_CURRENT_USER,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'),
        (winreg.HKEY_CURRENT_USER,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce'),
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'),
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce'),
    ]

    for hive, subkey in run_locations:
        key = _reg_open(hive, subkey)
        if key is None:
            continue
        try:
            idx = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(key, idx)
                    idx += 1
                except OSError:
                    break
                if not isinstance(data, str):
                    continue
                exe = _extract_exe_path(data)
                if not exe:
                    continue
                hive_str = _hive_name(hive)
                location_str = f'{hive_str}\\{subkey}'
                fp = {
                    'hive': hive,
                    'key':  subkey,
                    'value_name': name,
                }
                # Check suspicious path first
                if _under_suspicious(exe, susp_dirs):
                    is_temp = _under_temp_only(exe)
                    risk = 'high' if is_temp else 'medium'
                    if is_temp:
                        detail = (
                            'Executable running from a temporary directory — '
                            'common malware persistence location.'
                        )
                    else:
                        detail = (
                            'Executable running from AppData or Downloads — '
                            'potentially suspicious persistence location.'
                        )
                    findings.append(Finding(
                        category='startup',
                        title='Suspicious Run entry',
                        detail=detail,
                        location=location_str,
                        value=f'{name} = "{data}"',
                        risk=risk,
                        fixable=True,
                        fix_method='delete_reg_value',
                        fix_params=fp,
                    ))
                elif not os.path.isfile(exe):
                    findings.append(Finding(
                        category='startup',
                        title='Missing startup executable',
                        detail=(
                            f'The executable "{exe}" referenced in the startup '
                            'registry key does not exist on disk. This could be '
                            'a leftover entry from an uninstalled application or '
                            'a sign that malware has been removed but its startup '
                            'entry remains.'
                        ),
                        location=location_str,
                        value=f'{name} = "{data}"',
                        risk='medium',
                        fixable=True,
                        fix_method='delete_reg_value',
                        fix_params=fp,
                    ))
        finally:
            key.Close()

    return findings


def scan_system_integrity() -> list[Finding]:
    """Check Winlogon, AppInit_DLLs, IFEO debugger hijacks, and SafeBoot."""
    if sys.platform != 'win32':
        return []
    findings: list[Finding] = []

    # 1. Winlogon checks
    winlogon_key = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    shell_val, _ = _reg_get(winreg.HKEY_LOCAL_MACHINE, winlogon_key, 'Shell')
    if shell_val is not None:
        if shell_val.strip().lower() != 'explorer.exe':
            findings.append(Finding(
                category='integrity',
                title='Winlogon Shell hijack',
                detail=(
                    f'The Winlogon Shell value is "{shell_val}" instead of the '
                    'expected "Explorer.exe". Malware often replaces this to '
                    'launch before the Windows shell.'
                ),
                location=f'HKLM\\{winlogon_key}',
                value=f'Shell = "{shell_val}"',
                risk='high',
                fixable=True,
                fix_method='restore_reg_value',
                fix_params={
                    'hive': winreg.HKEY_LOCAL_MACHINE,
                    'key': winlogon_key,
                    'value_name': 'Shell',
                    'target_value': 'Explorer.exe',
                },
            ))

    userinit_val, _ = _reg_get(winreg.HKEY_LOCAL_MACHINE, winlogon_key,
                                'Userinit')
    if userinit_val is not None:
        if 'userinit.exe' not in userinit_val.lower():
            findings.append(Finding(
                category='integrity',
                title='Winlogon Userinit hijack',
                detail=(
                    f'The Winlogon Userinit value is "{userinit_val}" and does '
                    'not contain the expected "userinit.exe". This can indicate '
                    'a rootkit or persistent malware injecting into the logon '
                    'sequence.'
                ),
                location=f'HKLM\\{winlogon_key}',
                value=f'Userinit = "{userinit_val}"',
                risk='high',
                fixable=True,
                fix_method='restore_reg_value',
                fix_params={
                    'hive': winreg.HKEY_LOCAL_MACHINE,
                    'key': winlogon_key,
                    'value_name': 'Userinit',
                    'target_value': r'C:\Windows\system32\userinit.exe,',
                },
            ))

    # 2. AppInit_DLLs
    appinit_key = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows'
    appinit_val, _ = _reg_get(winreg.HKEY_LOCAL_MACHINE, appinit_key,
                               'AppInit_DLLs')
    if appinit_val is not None and appinit_val.strip() != '':
        findings.append(Finding(
            category='integrity',
            title='AppInit_DLLs injection',
            detail=(
                f'AppInit_DLLs is set to "{appinit_val}". This value causes '
                'the listed DLLs to be loaded into every process that loads '
                'user32.dll — a common code injection technique used by '
                'rootkits and adware.'
            ),
            location=f'HKLM\\{appinit_key}',
            value=f'AppInit_DLLs = "{appinit_val}"',
            risk='high',
            fixable=True,
            fix_method='restore_reg_value',
            fix_params={
                'hive': winreg.HKEY_LOCAL_MACHINE,
                'key': appinit_key,
                'value_name': 'AppInit_DLLs',
                'target_value': '',
            },
        ))

    # 3. IFEO Debugger hijacks
    ifeo_key = (
        r'SOFTWARE\Microsoft\Windows NT\CurrentVersion'
        r'\Image File Execution Options'
    )
    _ifeo_whitelist = {
        'vsjitdebugger.exe', 'ntsd.exe', 'windbg.exe',
        'drwtsn32.exe', 'dwwin.exe', 'procdump.exe', 'procdump64.exe',
    }
    ifeo_handle = _reg_open(winreg.HKEY_LOCAL_MACHINE, ifeo_key)
    if ifeo_handle is not None:
        try:
            sub_idx = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(ifeo_handle, sub_idx)
                    sub_idx += 1
                except OSError:
                    break
                sub_path = ifeo_key + '\\' + sub_name
                dbg_val, _ = _reg_get(winreg.HKEY_LOCAL_MACHINE, sub_path,
                                      'Debugger')
                if dbg_val is None:
                    continue
                dbg_exe = os.path.basename(
                    _extract_exe_path(dbg_val)
                ).lower()
                if dbg_exe in _ifeo_whitelist:
                    continue
                findings.append(Finding(
                    category='integrity',
                    title='IFEO Debugger hijack',
                    detail=(
                        f'"{sub_name}" has a Debugger value set to '
                        f'"{dbg_val}". Image File Execution Options debugger '
                        'entries intercept process launches and can be used '
                        'to prevent legitimate programs from running or to '
                        'launch malicious code in their place.'
                    ),
                    location=f'HKLM\\{sub_path}',
                    value=f'Debugger = "{dbg_val}"',
                    risk='high',
                    fixable=True,
                    fix_method='delete_reg_value',
                    fix_params={
                        'hive': winreg.HKEY_LOCAL_MACHINE,
                        'key': sub_path,
                        'value_name': 'Debugger',
                    },
                ))
        finally:
            ifeo_handle.Close()

    # 4. SafeBoot tampering
    for network_suffix in ('Minimal', 'Network'):
        sb_key = (
            r'SYSTEM\CurrentControlSet\Control\SafeBoot\\'
            + network_suffix
        )
        handle = _reg_open(winreg.HKEY_LOCAL_MACHINE, sb_key)
        if handle is None:
            findings.append(Finding(
                category='integrity',
                title=f'SafeBoot\\{network_suffix} key missing',
                detail=(
                    f'The registry key HKLM\\SYSTEM\\CurrentControlSet\\'
                    f'Control\\SafeBoot\\{network_suffix} is missing. Some '
                    'malware deletes SafeBoot keys to prevent users from '
                    'booting into Safe Mode to remove the infection.'
                ),
                location=(
                    f'HKLM\\SYSTEM\\CurrentControlSet\\Control'
                    f'\\SafeBoot\\{network_suffix}'
                ),
                value='(key not found)',
                risk='medium',
                fixable=False,
                fix_method='manual',
            ))
        else:
            handle.Close()

    return findings


def scan_hosts_file() -> list[Finding]:
    """Scan the hosts file for suspicious redirections."""
    findings: list[Finding] = []
    if sys.platform == 'win32':
        hosts_path = r'C:\Windows\System32\drivers\etc\hosts'
    else:
        hosts_path = '/etc/hosts'

    _well_known = [
        'google.com', 'microsoft.com', 'windows.com', 'windowsupdate.com',
        'update.microsoft.com', 'apple.com', 'facebook.com', 'amazon.com',
    ]
    _loopback_hosts = {'localhost', 'localhost.localdomain', 'broadcasthost',
                       'local', 'ip6-localhost', 'ip6-loopback',
                       'ip6-localnet', 'ip6-mcastprefix', 'ip6-allnodes',
                       'ip6-allrouters', 'ip6-allhosts'}

    try:
        with open(hosts_path, 'r', encoding='utf-8', errors='replace') as fh:
            lines = fh.readlines()
    except OSError:
        return []

    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        # Strip inline comments
        parts = line.split('#')[0].split()
        if len(parts) < 2:
            continue
        ip = parts[0]
        hostnames = parts[1:]

        # Skip standard loopback entries
        is_loopback_ip = ip in ('127.0.0.1', '0.0.0.0', '::1',
                                 'fe80::1%lo0', '255.255.255.255')
        if is_loopback_ip and all(h in _loopback_hosts for h in hostnames):
            continue

        for hostname in hostnames:
            # Check if it redirects a well-known domain
            hostname_lower = hostname.lower()
            is_wk = any(
                hostname_lower == wk or hostname_lower.endswith('.' + wk)
                for wk in _well_known
            )
            risk = 'high' if is_wk else 'medium'
            detail_prefix = (
                'A well-known domain is being redirected in the hosts file. '
                if is_wk else
                'A custom hosts entry was found. '
            )
            if is_wk:
                entry_detail = (
                    detail_prefix +
                    f'Line {lineno}: "{hostname}" -> {ip}. This may block '
                    'Windows Update, security software, or redirect you to '
                    'a malicious server.'
                )
            else:
                entry_detail = (
                    detail_prefix +
                    f'Line {lineno}: "{hostname}" -> {ip}. This could be a '
                    'legitimate developer entry or a malicious redirection.'
                )
            findings.append(Finding(
                category='hosts',
                title='Hosts file entry' + (' (well-known domain)' if is_wk
                                            else ''),
                detail=entry_detail,
                location=hosts_path,
                value=f'{ip}  {hostname}',
                risk=risk,
                fixable=False,
                fix_method='manual',
            ))

    return findings


def scan_running_processes() -> list[Finding]:
    """Scan running processes for executables in suspicious locations."""
    if sys.platform != 'win32':
        return []
    findings: list[Finding] = []
    susp_dirs = _suspicious_dirs()

    th_map = _win_enum_pids_toolhelp()   # {pid: exe_name} — no subprocess
    seen_paths: set[str] = set()

    for pid, name in th_map.items():
        if pid in (0, 4):
            continue
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED, False, pid)
        if not h or h == _INVALID_HANDLE:
            continue
        buf  = ctypes.create_unicode_buffer(512)
        size = _wt.DWORD(512)
        _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        _k32.CloseHandle(h)
        path = buf.value
        if not path:
            continue
        norm = os.path.normcase(os.path.realpath(path))
        if norm in seen_paths or not _under_suspicious(path, susp_dirs):
            continue
        seen_paths.add(norm)
        is_temp = _under_temp_only(path)
        risk = 'high' if is_temp else 'medium'
        findings.append(Finding(
            category='processes',
            title='Process running from suspicious location',
            detail=(
                f'Process "{name}" (PID {pid}) is running from '
                f'{"a temporary directory" if is_temp else "the Downloads folder"} '
                f'("{path}"). Legitimate applications rarely run from these '
                'locations; this is characteristic of malware or drive-by '
                'installer droppers.'
            ),
            location=path,
            value=f'{name}  (PID {pid})',
            risk=risk,
            fixable=False,
            fix_method='manual',
        ))

    return findings


def scan_scheduled_tasks() -> list[Finding]:
    """Scan scheduled tasks for executables in suspicious locations."""
    if sys.platform != 'win32':
        return []
    import xml.etree.ElementTree as ET
    findings: list[Finding] = []
    susp_dirs = _suspicious_dirs()

    tasks_dir = os.path.join(
        os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'Tasks'
    )
    if not os.path.isdir(tasks_dir):
        return []

    _NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'

    for dirpath, _, files in os.walk(tasks_dir):
        for fname in files:
            fpath = os.path.join(dirpath, fname)
            try:
                root_elem = ET.parse(fpath).getroot()
            except Exception:
                continue
            # Try namespaced and bare element names
            for ns_prefix in (f'{{{_NS}}}', ''):
                for exec_elem in root_elem.iter(f'{ns_prefix}Exec'):
                    cmd = (
                        exec_elem.findtext(f'{ns_prefix}Command') or
                        exec_elem.findtext('Command') or ''
                    ).strip()
                    if not cmd:
                        continue
                    exe = _extract_exe_path(cmd)
                    if not exe or not _under_suspicious(exe, susp_dirs):
                        continue
                    args = (
                        exec_elem.findtext(f'{ns_prefix}Arguments') or
                        exec_elem.findtext('Arguments') or ''
                    ).strip()
                    task_rel = os.path.relpath(fpath, tasks_dir)
                    findings.append(Finding(
                        category='tasks',
                        title='Scheduled task running from suspicious location',
                        detail=(
                            f'Scheduled task "{fname}" executes "{exe}" which is '
                            'located in a temporary or Downloads directory. Malware '
                            'commonly creates scheduled tasks pointing to these '
                            'directories to survive reboots.'
                        ),
                        location=task_rel,
                        value=cmd + (f'  {args}'.rstrip()),
                        risk='high',
                        fixable=False,
                        fix_method='manual',
                    ))
                    break   # one exec action per task file is enough

    return findings


def scan_lsp_providers() -> list[Finding]:
    """Scan Winsock LSP catalog for non-system provider DLLs."""
    if sys.platform != 'win32':
        return []
    findings: list[Finding] = []

    system32 = os.path.normcase(
        os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
    )
    syswow64 = os.path.normcase(
        os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'SysWOW64')
    )

    # Winsock catalog registry locations (64-bit and 32-bit entries)
    catalog_keys = [
        r'SYSTEM\CurrentControlSet\Services\WinSock2\Parameters'
        r'\Protocol_Catalog9\Catalog_Entries64',
        r'SYSTEM\CurrentControlSet\Services\WinSock2\Parameters'
        r'\Protocol_Catalog9\Catalog_Entries',
    ]

    seen: set[str] = set()

    for cat_path in catalog_keys:
        cat_key = _reg_open(winreg.HKEY_LOCAL_MACHINE, cat_path)
        if not cat_key:
            continue
        try:
            idx = 0
            while True:
                try:
                    entry_name = winreg.EnumKey(cat_key, idx)
                    idx += 1
                except OSError:
                    break
                entry_key = _reg_open(
                    winreg.HKEY_LOCAL_MACHINE,
                    cat_path + '\\' + entry_name,
                )
                if not entry_key:
                    continue
                try:
                    packed, _ = winreg.QueryValueEx(entry_key, 'PackedCatalogItem')
                    if not packed:
                        continue
                    # PackedCatalogItem layout: char szLibraryPath[MAX_PATH+4] then WSAPROTOCOL_INFOW
                    # DLL path is a null-terminated ANSI string at offset 0
                    null_pos = packed.find(b'\x00')
                    if null_pos < 0 or null_pos > 264:
                        null_pos = min(264, len(packed))
                    dll_path = packed[:null_pos].decode('mbcs', errors='replace').strip()
                    if not dll_path:
                        continue
                    norm = os.path.normcase(dll_path)
                    if norm in seen:
                        continue
                    seen.add(norm)
                    if norm.startswith(system32) or norm.startswith(syswow64):
                        continue
                    provider_name = os.path.basename(dll_path)
                    findings.append(Finding(
                        category='network',
                        title='Non-system LSP provider',
                        detail=(
                            f'Winsock LSP provider "{provider_name}" uses '
                            f'"{dll_path}" which is not located in System32 or '
                            'SysWOW64. Third-party LSP providers can intercept all '
                            'network traffic and are frequently used by adware and '
                            'spyware.'
                        ),
                        location='Winsock Catalog',
                        value=f'{provider_name} → {dll_path}',
                        risk='medium',
                        fixable=False,
                        fix_method='manual',
                    ))
                except (OSError, UnicodeDecodeError):
                    pass
                finally:
                    entry_key.Close()
        finally:
            cat_key.Close()

    return findings


# ─────────────────────────────────────────────────────────────────────────────

if sys.platform == 'win32':
    _INDICATORS = [
        (winreg.HKEY_CURRENT_USER,  r'Software\Conduit',
         'Conduit Toolbar',           'medium'),
        (winreg.HKEY_CURRENT_USER,  r'Software\Babylon',
         'Babylon Toolbar',            'medium'),
        (winreg.HKEY_CURRENT_USER,  r'Software\BabylonToolbar',
         'Babylon Toolbar (variant)',  'high'),
        (winreg.HKEY_CURRENT_USER,  r'Software\DataMngr',
         'DataMngr adware',            'high'),
        (winreg.HKEY_CURRENT_USER,  r'Software\Sweetpacks',
         'SweetPacks Toolbar',         'medium'),
        (winreg.HKEY_CURRENT_USER,  r'Software\Somoto',
         'Somoto BetterInstaller',     'high'),
        (winreg.HKEY_CURRENT_USER,  r'Software\Wajam',
         'Wajam social search adware', 'high'),
        (winreg.HKEY_CURRENT_USER,  r'Software\istartsurf',
         'iStartSurf hijacker',        'high'),
        (winreg.HKEY_CURRENT_USER,  r'Software\delta',
         'Delta Search hijacker',      'high'),
        (winreg.HKEY_CURRENT_USER,  r'Software\Trovi',
         'Trovi hijacker',             'high'),
        (winreg.HKEY_CURRENT_USER,  r'Software\SearchProtect',
         'Search Protect',             'high'),
        (winreg.HKEY_CURRENT_USER,  r'Software\Iminent',
         'Iminent toolbar',            'medium'),
        (winreg.HKEY_CURRENT_USER,  r'Software\InstalledBrowserExtensions',
         'PUP Browser Extension registry trace', 'medium'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Conduit',
         'Conduit Toolbar (system)',   'medium'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Wow6432Node\Conduit',
         'Conduit Toolbar (32-bit)',   'medium'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Wajam',
         'Wajam (system)',             'high'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Trovi',
         'Trovi (system)',             'high'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\SearchProtect',
         'Search Protect (system)',    'high'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Wow6432Node\Wajam',
         'Wajam 32-bit',               'high'),
    ]
else:
    _INDICATORS = []


def scan_known_indicators() -> list[Finding]:
    """Check for registry keys associated with known adware / PUP families."""
    if sys.platform != 'win32':
        return []
    findings: list[Finding] = []
    for hive, path, label, risk in _INDICATORS:
        key = _reg_open(hive, path)
        if key is None:
            continue
        key.Close()
        hive_str = _hive_name(hive)
        findings.append(Finding(
            category='indicators',
            title=f'Known PUP/adware indicator: {label}',
            detail=(
                f'The registry key {hive_str}\\{path} was found. '
                f'This key is associated with "{label}", a known potentially '
                'unwanted program (PUP) or adware family. Even if the '
                'main application has been uninstalled, these remnants can '
                'indicate prior infection or incomplete removal.'
            ),
            location=f'{hive_str}\\{path}',
            value='(key exists)',
            risk=risk,
            fixable=True,
            fix_method='delete_reg_key',
            fix_params={'hive': hive, 'key': path},
        ))
    return findings


def scan_browser_hijacks() -> list[Finding]:
    """Scan for browser hijacks via IE settings and Chrome/Edge policies."""
    if sys.platform != 'win32':
        return []
    findings: list[Finding] = []

    # 1. IE Start Page
    ie_key = r'Software\Microsoft\Internet Explorer\Main'
    start_page, _ = _reg_get(winreg.HKEY_CURRENT_USER, ie_key, 'Start Page')
    if start_page and isinstance(start_page, str):
        sp = start_page.strip().lower()
        benign = (
            sp in ('', 'about:blank', 'about:newtab') or
            sp.startswith('https://www.msn.com') or
            sp.startswith('about:newtab')
        )
        if not benign:
            findings.append(Finding(
                category='browser',
                title='IE Start Page hijack',
                detail=(
                    f'Internet Explorer Start Page is set to "{start_page}". '
                    'A non-default start page can indicate a browser hijacker '
                    'that redirects the home page to a monetised or malicious '
                    'search engine.'
                ),
                location=f'HKCU\\{ie_key}',
                value=f'Start Page = "{start_page}"',
                risk='high',
                fixable=True,
                fix_method='restore_reg_value',
                fix_params={
                    'hive': winreg.HKEY_CURRENT_USER,
                    'key': ie_key,
                    'value_name': 'Start Page',
                    'target_value': 'about:blank',
                },
            ))

    # 2. Chrome policies
    chrome_policy_key = r'SOFTWARE\Policies\Google\Chrome'
    chrome_handle = _reg_open(winreg.HKEY_LOCAL_MACHINE, chrome_policy_key)
    if chrome_handle is not None:
        try:
            values: list[str] = []
            idx = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(chrome_handle, idx)
                    values.append(f'{name} = {data!r}')
                    idx += 1
                except OSError:
                    break
        finally:
            chrome_handle.Close()
        value_summary = '; '.join(values[:5]) or '(no values)'
        findings.append(Finding(
            category='browser',
            title='Chrome group policy detected',
            detail=(
                'Registry-based Chrome group policies are present under '
                f'HKLM\\{chrome_policy_key}. These policies can force '
                'specific home pages, search engines, or disable security '
                'settings. Adware and corporate management tools both use '
                'this mechanism.'
            ),
            location=f'HKLM\\{chrome_policy_key}',
            value=value_summary,
            risk='medium',
            fixable=False,
            fix_method='manual',
        ))

    # 3. Edge policies
    edge_policy_key = r'SOFTWARE\Policies\Microsoft\Edge'
    edge_handle = _reg_open(winreg.HKEY_LOCAL_MACHINE, edge_policy_key)
    if edge_handle is not None:
        try:
            values = []
            idx = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(edge_handle, idx)
                    values.append(f'{name} = {data!r}')
                    idx += 1
                except OSError:
                    break
        finally:
            edge_handle.Close()
        value_summary = '; '.join(values[:5]) or '(no values)'
        findings.append(Finding(
            category='browser',
            title='Edge group policy detected',
            detail=(
                'Registry-based Microsoft Edge group policies are present '
                f'under HKLM\\{edge_policy_key}. These policies can force '
                'specific home pages, search engines, or disable security '
                'settings.'
            ),
            location=f'HKLM\\{edge_policy_key}',
            value=value_summary,
            risk='medium',
            fixable=False,
            fix_method='manual',
        ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Rootkit detection
# ─────────────────────────────────────────────────────────────────────────────

def scan_hidden_processes() -> list[Finding]:
    """Compare two independent process-enumeration APIs and walk PID space.

    A discrepancy between EnumProcesses and CreateToolhelp32Snapshot, or a
    PID that can be opened via OpenProcess but doesn't appear in either
    enumeration, indicates a userland hook hiding the process.
    """
    if sys.platform != 'win32':
        return []
    findings: list[Finding] = []

    psapi_pids: set[int] = _win_enum_pids_psapi()
    th_map:  dict[int, str] = _win_enum_pids_toolhelp()
    th_pids: set[int] = set(th_map)

    # PIDs present in one source but not the other (exclude PID 0 / 4 = Idle/System)
    _system_pids = {0, 4}
    only_in_psapi = psapi_pids - th_pids  - _system_pids
    only_in_th    = th_pids   - psapi_pids - _system_pids

    for pid in sorted(only_in_psapi):
        findings.append(Finding(
            category='rootkit',
            title='Process hidden from Toolhelp (visible via EnumProcesses)',
            detail=(
                f'PID {pid} appears in EnumProcesses/psapi but NOT in '
                'CreateToolhelp32Snapshot. A rootkit can hook one API while '
                'leaving the other intact. Verify with an external scanner.'
            ),
            location=f'PID {pid}',
            value=str(pid),
            risk='high',
            fixable=False,
            fix_method='manual',
        ))

    for pid in sorted(only_in_th):
        name = th_map.get(pid, '?')
        findings.append(Finding(
            category='rootkit',
            title='Process hidden from EnumProcesses (visible via Toolhelp)',
            detail=(
                f'PID {pid} ({name}) appears in CreateToolhelp32Snapshot but '
                'NOT in EnumProcesses. API discrepancy is a classic sign of a '
                'userland rootkit hook.'
            ),
            location=f'PID {pid}',
            value=f'{name}  (PID {pid})',
            risk='high',
            fixable=False,
            fix_method='manual',
        ))

    # PID walk — find processes openable but absent from both enumeration APIs
    known_all = psapi_pids | th_pids
    for pid, name, path in _win_pid_walk(known_all):
        findings.append(Finding(
            category='rootkit',
            title='Hidden process — not visible to any enumeration API',
            detail=(
                f'PID {pid} ({name}) can be opened with OpenProcess '
                '(PROCESS_QUERY_LIMITED_INFORMATION) but does NOT appear in '
                'either EnumProcesses or CreateToolhelp32Snapshot. This is a '
                'strong indicator of a kernel-mode or userland rootkit actively '
                'hiding the process from the task manager and security tools.'
            ),
            location=path if path else f'PID {pid}',
            value=f'{name}  (PID {pid})',
            risk='high',
            fixable=False,
            fix_method='manual',
        ))

    return findings


def scan_kernel_drivers() -> list[Finding]:
    """Enumerate loaded kernel modules and flag suspicious driver locations.

    Checks:
    • Drivers loaded from user-writable / temp / non-Windows locations.
    • Drivers with no corresponding Services registry entry (ghost drivers).
    • Service entries whose ImagePath points to a suspicious directory.
    """
    if sys.platform != 'win32':
        return []
    findings: list[Finding] = []

    sys_root = os.environ.get('SystemRoot', r'C:\Windows')
    susp_dirs = _suspicious_dirs()
    services_subkey = r'SYSTEM\CurrentControlSet\Services'

    # ── Build set of known service names from registry ────────────────────────
    registered: set[str] = set()
    svc_key = _reg_open(winreg.HKEY_LOCAL_MACHINE, services_subkey)
    if svc_key:
        idx = 0
        while True:
            try:
                registered.add(winreg.EnumKey(svc_key, idx).lower())
                idx += 1
            except OSError:
                break
        svc_key.Close()

    # ── Check loaded drivers via EnumDeviceDrivers ────────────────────────────
    norm_sysroot = os.path.normcase(sys_root)
    norm_wintemp = os.path.normcase(os.path.join(sys_root, 'Temp'))

    for raw in _win_enum_drivers():
        real = _kernel_path_to_real(raw)
        norm = os.path.normcase(real)
        fname_no_ext = os.path.splitext(os.path.basename(real))[0].lower()

        if norm.startswith(norm_sysroot):
            # Inside Windows dir — flag if in Windows\Temp specifically
            if norm.startswith(norm_wintemp):
                findings.append(Finding(
                    category='rootkit',
                    title='Driver loaded from Windows\\Temp',
                    detail=(
                        f'Kernel driver "{os.path.basename(real)}" is loaded '
                        'from the Windows\\Temp directory. Legitimate drivers '
                        'reside in System32\\drivers.'
                    ),
                    location=raw,
                    value=os.path.basename(real),
                    risk='high',
                    fixable=False,
                    fix_method='manual',
                ))
            continue  # otherwise OK inside Windows

        # Outside Windows directory entirely
        if not os.path.isabs(real):
            continue  # kernel pseudo-device (e.g. \Device\Mup) — skip

        if _under_suspicious(real, susp_dirs):
            findings.append(Finding(
                category='rootkit',
                title='Driver loaded from user-writable location',
                detail=(
                    f'Kernel driver "{os.path.basename(real)}" is loaded from '
                    f'a user-writable directory. Legitimate drivers reside in '
                    'System32\\drivers.'
                ),
                location=raw,
                value=os.path.basename(real),
                risk='high',
                fixable=False,
                fix_method='manual',
            ))
        elif fname_no_ext and fname_no_ext not in registered:
            findings.append(Finding(
                category='rootkit',
                title='Unregistered driver outside Windows directory',
                detail=(
                    f'"{os.path.basename(real)}" is a loaded kernel driver with '
                    'no matching Services registry entry, loaded from outside the '
                    'Windows directory. This pattern is consistent with a ghost '
                    'driver injected by a rootkit.'
                ),
                location=raw,
                value=os.path.basename(real),
                risk='high',
                fixable=False,
                fix_method='manual',
            ))
        else:
            findings.append(Finding(
                category='rootkit',
                title='Driver loaded from non-standard location',
                detail=(
                    f'Kernel driver "{os.path.basename(real)}" is loaded from '
                    f'"{real}" which is outside the Windows directory. While '
                    'some third-party drivers legitimately reside elsewhere, '
                    'this warrants investigation.'
                ),
                location=raw,
                value=os.path.basename(real),
                risk='medium',
                fixable=False,
                fix_method='manual',
            ))

    # ── Scan Services for kernel drivers with suspicious ImagePath ────────────
    svc_key2 = _reg_open(winreg.HKEY_LOCAL_MACHINE, services_subkey)
    if svc_key2:
        idx = 0
        while True:
            try:
                svc_name = winreg.EnumKey(svc_key2, idx)
                idx += 1
            except OSError:
                break
            child = _reg_open(
                winreg.HKEY_LOCAL_MACHINE,
                services_subkey + '\\' + svc_name,
            )
            if not child:
                continue
            try:
                try:
                    svc_type, _ = winreg.QueryValueEx(child, 'Type')
                except OSError:
                    continue
                if svc_type not in (1, 2):  # 1=kernel driver, 2=FS driver
                    continue
                try:
                    img_path, _ = winreg.QueryValueEx(child, 'ImagePath')
                except OSError:
                    continue
                if not img_path:
                    continue
                real_img = _kernel_path_to_real(img_path.strip().strip('"'))
                if _under_suspicious(real_img, susp_dirs):
                    findings.append(Finding(
                        category='rootkit',
                        title='Kernel driver service with suspicious ImagePath',
                        detail=(
                            f'Service "{svc_name}" (Type={svc_type}) has its '
                            'ImagePath pointing to a suspicious location. '
                            'Legitimate kernel drivers are installed in '
                            'System32\\drivers.'
                        ),
                        location=f'HKLM\\{services_subkey}\\{svc_name}',
                        value=f'ImagePath = "{img_path}"',
                        risk='high',
                        fixable=True,
                        fix_method='delete_reg_key',
                        fix_params={
                            'hive': winreg.HKEY_LOCAL_MACHINE,
                            'key':  services_subkey + '\\' + svc_name,
                        },
                    ))
            finally:
                child.Close()
        svc_key2.Close()

    return findings


# Known-good MBR bootstrap first-byte patterns
_KNOWN_GOOD_MBR: list[bytes] = [
    bytes([0x33, 0xC0, 0x8E, 0xD0, 0xBC, 0x00, 0x7C]),  # Windows Vista/7/8/10
    bytes([0xFA, 0x33, 0xC0, 0x8E, 0xD0, 0xBC]),         # Windows NT/2000/XP
    bytes([0xEB, 0x63, 0x90]),                             # GRUB 2
    bytes([0xEB, 0x5C, 0x90]),                             # GRUB legacy / some OEM
    bytes([0xEB, 0x52, 0x90]),                             # syslinux
    bytes([0xEB, 0x58, 0x90]),                             # syslinux variant
    bytes([0x33, 0xC0, 0x8E, 0xC0, 0x8E, 0xD8]),         # Some OEM MBRs
]

# Known bootkit byte patterns at specific offsets in bootstrap code
_BOOTKIT_SIGS: list[tuple[str, int, bytes]] = [
    # (name, offset, signature)
    ('Alureon/TDL4',    0, bytes([0x33, 0xED, 0x8E, 0xD5, 0xBC, 0x00, 0x7C])),
    ('Pihar/TDL4 v3',   0, bytes([0xEB, 0x5A, 0x90, 0x54, 0x44, 0x4C, 0x34])),  # …"TDL4"
    ('Mebroot/Sinowal', 0, bytes([0x33, 0xC9, 0x8E, 0xD1, 0xBC, 0xF4, 0x7B])),
    ('Rovnix',          3, bytes([0x52, 0x4F, 0x56, 0x4E, 0x49, 0x58])),          # "ROVNIX"
    ('MZ in MBR',       0, bytes([0x4D, 0x5A])),                                  # PE header in MBR
]


def scan_mbr() -> list[Finding]:
    """Read the Master Boot Record and check for bootkit indicators."""
    if sys.platform != 'win32':
        return []
    findings: list[Finding] = []

    try:
        with open(r'\\.\PhysicalDrive0', 'rb') as fh:
            mbr = fh.read(512)
    except PermissionError:
        return [Finding(
            category='rootkit',
            title='MBR scan requires Administrator',
            detail=(
                'Reading the Master Boot Record requires Administrator '
                'privileges. Re-run UniTool as Administrator for MBR analysis.'
            ),
            location=r'\\.\PhysicalDrive0',
            value='(access denied)',
            risk='info',
            fixable=False,
            fix_method='manual',
        )]
    except OSError:
        return []

    if len(mbr) < 512:
        return []

    # 1. Boot signature
    if mbr[510] != 0x55 or mbr[511] != 0xAA:
        findings.append(Finding(
            category='rootkit',
            title='Invalid MBR boot signature',
            detail=(
                'The Master Boot Record is missing the 0x55AA signature at '
                'bytes 510-511. This indicates MBR corruption or a bootkit '
                'that has overwritten the standard bootstrap code.'
            ),
            location=r'\\.\PhysicalDrive0 (sector 0, offset 510)',
            value=f'Found: 0x{mbr[510]:02X} 0x{mbr[511]:02X}  (expected: 0x55 0xAA)',
            risk='high',
            fixable=False,
            fix_method='manual',
        ))

    bootstrap = mbr[:440]

    # 2. Known bootkit signatures
    for bk_name, offset, sig in _BOOTKIT_SIGS:
        if bootstrap[offset:offset + len(sig)] == sig:
            findings.append(Finding(
                category='rootkit',
                title=f'Bootkit signature detected: {bk_name}',
                detail=(
                    f'MBR bootstrap code matches a known {bk_name} bootkit '
                    'signature. The MBR has likely been replaced with malicious '
                    'code that runs before the OS, giving the rootkit full '
                    'control before any security software loads.'
                ),
                location=r'\\.\PhysicalDrive0 (sector 0)',
                value=('Signature at offset '
                       f'{offset}: {" ".join(f"{b:02X}" for b in sig)}'),
                risk='high',
                fixable=False,
                fix_method='manual',
            ))

    # 3. Partition table: check for GPT protective MBR (type 0xEE)
    is_gpt = any(mbr[446 + i * 16 + 4] == 0xEE for i in range(4))

    # 4. For legacy MBR (not GPT), flag unrecognized bootstrap code
    if not is_gpt and not any(f.risk == 'high' for f in findings):
        known_good = any(
            bootstrap[:len(pat)] == pat for pat in _KNOWN_GOOD_MBR
        )
        if not known_good:
            first8 = ' '.join(f'{b:02X}' for b in mbr[:8])
            findings.append(Finding(
                category='rootkit',
                title='Unrecognized MBR bootstrap code',
                detail=(
                    'The MBR bootstrap code does not match any known standard '
                    'pattern (Windows Vista/7/8/10, GRUB, syslinux). '
                    'This may indicate a bootkit or a custom/OEM boot loader. '
                    'Manual verification is recommended.'
                ),
                location=r'\\.\PhysicalDrive0 (sector 0)',
                value=f'First 8 bytes: {first8}',
                risk='medium',
                fixable=False,
                fix_method='manual',
            ))

    # 5. GPT/UEFI info (normal for modern Windows, but mention UEFI rootkit risk)
    if is_gpt and not findings:
        findings.append(Finding(
            category='rootkit',
            title='GPT / UEFI boot detected — MBR is protective stub',
            detail=(
                'The disk uses GUID Partition Table (GPT) with a protective MBR '
                '— normal for UEFI-booted Windows installations. The actual boot '
                'is managed by UEFI firmware and the EFI System Partition (ESP). '
                'Note: UEFI-level bootkits (e.g. FinFisher UEFI) cannot be '
                'detected from a running OS and require firmware-level scanning.'
            ),
            location=r'\\.\PhysicalDrive0 (sector 0)',
            value='Partition type 0xEE — GPT protective MBR',
            risk='info',
            fixable=False,
            fix_method='manual',
        ))

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Fix engine
# ─────────────────────────────────────────────────────────────────────────────

def fix_finding(f: Finding) -> tuple[bool, str]:
    """Apply the automated fix for a Finding. Returns (success, error_msg)."""
    if f.fix_method == 'manual':
        return False, 'Manual action required'

    if f.fix_method == 'delete_reg_value':
        if sys.platform != 'win32':
            return False, 'Registry operations not supported on this platform'
        hive = f.fix_params.get('hive')
        key_path = f.fix_params.get('key', '')
        value_name = f.fix_params.get('value_name', '')
        try:
            key = winreg.OpenKey(
                hive, key_path, 0,
                winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY,
            )
            try:
                winreg.DeleteValue(key, value_name)
            finally:
                key.Close()
            f.fixed = True
            return True, ''
        except OSError as exc:
            return False, str(exc)

    if f.fix_method == 'delete_reg_key':
        if sys.platform != 'win32':
            return False, 'Registry operations not supported on this platform'
        hive = f.fix_params.get('hive')
        key_path = f.fix_params.get('key', '')
        try:
            _reg_delete_key_recursive(hive, key_path)
            f.fixed = True
            return True, ''
        except OSError as exc:
            return False, str(exc)

    if f.fix_method == 'restore_reg_value':
        if sys.platform != 'win32':
            return False, 'Registry operations not supported on this platform'
        hive = f.fix_params.get('hive')
        key_path = f.fix_params.get('key', '')
        value_name = f.fix_params.get('value_name', '')
        target_value = f.fix_params.get('target_value', '')
        try:
            key = winreg.OpenKey(
                hive, key_path, 0,
                winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY,
            )
            try:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ,
                                  target_value)
            finally:
                key.Close()
            f.fixed = True
            return True, ''
        except OSError as exc:
            return False, str(exc)

    if f.fix_method == 'delete_file':
        file_path = f.fix_params.get('path', f.location)
        try:
            os.remove(file_path)
            f.fixed = True
            return True, ''
        except OSError as exc:
            return False, str(exc)

    return False, f'Unknown fix_method: {f.fix_method!r}'


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

_SCAN_STEPS = [
    (scan_startup_persistence, 'Checking startup persistence…'),
    (scan_system_integrity,    'Checking system integrity…'),
    (scan_hosts_file,          'Scanning hosts file…'),
    (scan_running_processes,   'Examining running processes…'),
    (scan_scheduled_tasks,     'Scanning scheduled tasks…'),
    (scan_lsp_providers,       'Checking LSP providers…'),
    (scan_known_indicators,    'Checking known malware indicators…'),
    (scan_browser_hijacks,     'Checking browser hijacks…'),
    (scan_hidden_processes,    'Scanning for hidden processes…'),
    (scan_kernel_drivers,      'Examining loaded kernel drivers…'),
    (scan_mbr,                 'Scanning Master Boot Record…'),
]

_RISK_ORDER = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}


def scan_all(
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> list[Finding]:
    """Run all scan functions and return findings sorted by risk."""
    total = len(_SCAN_STEPS)
    all_findings: list[Finding] = []

    for step, (fn, label) in enumerate(_SCAN_STEPS):
        if progress_callback is not None:
            progress_callback(step, total, label)
        try:
            results = fn()
            all_findings.extend(results)
        except Exception:
            pass  # one failing scanner must not abort the rest

    if progress_callback is not None:
        progress_callback(total, total, 'Scan complete.')

    all_findings.sort(key=lambda f: _RISK_ORDER.get(f.risk, 99))
    return all_findings


# ─────────────────────────────────────────────────────────────────────────────
# Report generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_report(findings: list[Finding]) -> str:
    """Generate a ComboFix-style plain-text report."""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        sys_version = platform.version()
    except Exception:
        sys_version = 'Unknown'
    try:
        username = os.getlogin()
    except Exception:
        username = os.environ.get('USERNAME', os.environ.get('USER', 'Unknown'))

    counts = {'high': 0, 'medium': 0, 'low': 0, 'info': 0}
    for f in findings:
        if f.risk in counts:
            counts[f.risk] += 1

    wide = '═' * 59
    thin = '━' * 59

    lines: list[str] = [
        wide,
        '  UniTool System Check Report',
        f'  Generated: {now}',
        f'  System: {sys_version}',
        f'  User: {username}',
        wide,
        '',
        'SUMMARY',
        f'  High risk:    {counts["high"]}',
        f'  Medium risk:  {counts["medium"]}',
        f'  Low risk:     {counts["low"]}',
        f'  Info:         {counts["info"]}',
        f'  Total:        {len(findings)}',
        '',
    ]

    def _category_label(cat: str) -> str:
        for key, icon, _ in CATEGORIES:
            if key == cat:
                return f'{icon} {cat.capitalize()}'
        return cat.capitalize()

    for risk_level in ('high', 'medium', 'low', 'info'):
        level_findings = [f for f in findings if f.risk == risk_level]
        if not level_findings:
            continue
        lines.append(thin)
        lines.append(f'[{risk_level.upper()} RISK]')
        lines.append(thin)
        lines.append('')
        for f in level_findings:
            lines.append(f'Category: {_category_label(f.category)}')
            lines.append(f'Finding:  {f.title}')
            lines.append(f'Location: {f.location}')
            lines.append(f'Value:    {f.value}')
            lines.append(f'Detail:   {f.detail}')
            lines.append(f'Fix:      {f.fix_method}')
            lines.append(f'Fixed:    {"Yes" if f.fixed else "No"}')
            lines.append('')

    lines += [wide, '  End of Report', wide]
    return '\n'.join(lines)
