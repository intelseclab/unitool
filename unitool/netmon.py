"""
unitool/netmon.py
Network monitor backend — connection enumeration, geo-resolution, firewall control.
"""

import json
import os
import platform
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

import psutil
from PyQt6.QtCore import QThread, pyqtSignal

_CNW = 0x08000000  # CREATE_NO_WINDOW
_OS  = platform.system()  # 'Windows' | 'Darwin' | 'Linux'

# ── State display ──────────────────────────────────────────────────────────────

STATE_COLOR: dict[str, str] = {
    'ESTABLISHED': '#4CDE9A',
    'LISTEN':      '#4CC2FF',
    'TIME_WAIT':   '#FFA040',
    'CLOSE_WAIT':  '#FF6060',
    'SYN_SENT':    '#FFD040',
    'SYN_RECV':    '#FFD040',
    'FIN_WAIT1':   '#FF8855',
    'FIN_WAIT2':   '#FF8855',
    'LAST_ACK':    '#FF8855',
    'CLOSING':     '#FF8855',
    'CLOSED':      '#666666',
    'NONE':        '#666666',
}

COUNTRY_FLAG: dict[str, str] = {
    'US': '🇺🇸', 'GB': '🇬🇧', 'DE': '🇩🇪', 'FR': '🇫🇷', 'NL': '🇳🇱',
    'TR': '🇹🇷', 'CN': '🇨🇳', 'RU': '🇷🇺', 'JP': '🇯🇵', 'KR': '🇰🇷',
    'CA': '🇨🇦', 'AU': '🇦🇺', 'BR': '🇧🇷', 'IN': '🇮🇳', 'SG': '🇸🇬',
    'SE': '🇸🇪', 'NO': '🇳🇴', 'FI': '🇫🇮', 'CH': '🇨🇭', 'IE': '🇮🇪',
}


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class Connection:
    pid:          int
    process_name: str
    process_path: str
    protocol:     str      # TCP / UDP
    local_addr:   str
    local_port:   int
    remote_addr:  str
    remote_port:  int
    state:        str
    # filled by geo resolver
    remote_host:         str = ''
    remote_country:      str = ''
    remote_country_code: str = ''
    remote_city:         str = ''
    remote_org:          str = ''
    # meta
    first_seen: str = field(default_factory=lambda: datetime.now().strftime('%H:%M:%S'))

    @property
    def conn_id(self) -> str:
        return f'{self.protocol}|{self.local_addr}:{self.local_port}|{self.remote_addr}:{self.remote_port}|{self.pid}'

    @property
    def state_color(self) -> str:
        return STATE_COLOR.get(self.state, '#666666')

    @property
    def flag(self) -> str:
        return COUNTRY_FLAG.get(self.remote_country_code, '🌐')

    @property
    def remote_display(self) -> str:
        host = self.remote_host or self.remote_addr
        return f'{host}:{self.remote_port}'

    @property
    def local_display(self) -> str:
        return f'{self.local_addr}:{self.local_port}'

    @property
    def geo_display(self) -> str:
        parts = [self.flag]
        if self.remote_city:
            parts.append(self.remote_city)
        if self.remote_country:
            parts.append(self.remote_country)
        return '  '.join(parts) if len(parts) > 1 else ''

    @property
    def org_display(self) -> str:
        org = self.remote_org
        # Strip leading AS number (e.g. "AS15169 Google LLC" → "Google LLC")
        if org and org.startswith('AS'):
            parts = org.split(' ', 1)
            return parts[1] if len(parts) > 1 else org
        return org


# ── Geo / DNS cache ────────────────────────────────────────────────────────────

class _IPCache:
    def __init__(self):
        self._data:    dict[str, dict] = {}
        self._pending: set[str]        = set()
        self._lock = threading.Lock()

    def get(self, ip: str) -> dict | None:
        with self._lock:
            return self._data.get(ip)

    def put(self, ip: str, info: dict):
        with self._lock:
            self._data[ip] = info
            self._pending.discard(ip)

    def mark_pending(self, ip: str) -> bool:
        """Returns True if we should start resolving (wasn't already pending/done)."""
        with self._lock:
            if ip in self._data or ip in self._pending:
                return False
            self._pending.add(ip)
            return True


_ip_cache = _IPCache()

_PRIVATE_PREFIXES = (
    '127.', '10.', '192.168.', '169.254.', '::1', 'fe80',
    '0.0.0.0', '::'
)


def _is_private(ip: str) -> bool:
    return any(ip.startswith(p) for p in _PRIVATE_PREFIXES)


# ── Geo resolver worker ────────────────────────────────────────────────────────

class GeoWorker(QThread):
    resolved = pyqtSignal(str, dict)   # ip, info

    def __init__(self, ip: str, parent=None):
        super().__init__(parent)
        self._ip = ip

    def run(self):
        ip   = self._ip
        info: dict = {}

        # DNS hostname — run with a 3-second cap because gethostbyaddr has no
        # built-in timeout and can stall for 10–30 s on unresponsive PTR records.
        host_result: list = []
        def _resolve():
            try:
                host_result.append(socket.gethostbyaddr(ip)[0])
            except Exception:
                host_result.append('')
        t = threading.Thread(target=_resolve, daemon=True)
        t.start()
        t.join(3.0)
        info['host'] = host_result[0] if host_result else ''

        # Geo + org (ip-api.com — free, 45 req/min)
        if not _is_private(ip):
            try:
                url = (
                    f'http://ip-api.com/json/{ip}'
                    '?fields=status,country,countryCode,city,org,as'
                )
                req = urllib.request.Request(url, headers={'User-Agent': 'UniTool/1.0'})
                with urllib.request.urlopen(req, timeout=6) as r:
                    data = json.loads(r.read().decode())
                if data.get('status') == 'success':
                    info.update(data)
            except Exception:
                pass

        _ip_cache.put(ip, info)
        self.resolved.emit(ip, info)


# ── Connection poll worker ─────────────────────────────────────────────────────

class NetMonWorker(QThread):
    updated = pyqtSignal(list)    # list[Connection]

    def __init__(self, interval: float = 1.5, parent=None):
        super().__init__(parent)
        self._interval = interval
        self._running  = False

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        while self._running:
            try:
                self.updated.emit(self._collect())
            except Exception:
                pass
            time.sleep(self._interval)

    def _collect(self) -> list[Connection]:
        proc_map: dict[int, tuple[str, str]] = {}
        for p in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                i = p.info
                proc_map[i['pid']] = (i['name'] or '?', i['exe'] or '')
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        result: list[Connection] = []
        try:
            raw = psutil.net_connections(kind='all')
        except psutil.AccessDenied:
            return result

        for c in raw:
            if not c.raddr:
                continue

            pid        = c.pid or 0
            name, path = proc_map.get(pid, ('System', ''))
            proto      = 'TCP' if c.type == socket.SOCK_STREAM else 'UDP'
            state      = (c.status or 'NONE').upper()

            conn = Connection(
                pid=pid, process_name=name, process_path=path,
                protocol=proto,
                local_addr=c.laddr.ip   if c.laddr else '',
                local_port=c.laddr.port if c.laddr else 0,
                remote_addr=c.raddr.ip   if c.raddr else '',
                remote_port=c.raddr.port if c.raddr else 0,
                state=state,
            )

            geo = _ip_cache.get(conn.remote_addr)
            if geo:
                conn.remote_host         = geo.get('host', '')
                conn.remote_country      = geo.get('country', '')
                conn.remote_country_code = geo.get('countryCode', '')
                conn.remote_city         = geo.get('city', '')
                conn.remote_org          = geo.get('org') or geo.get('as', '')
            result.append(conn)

        result.sort(key=lambda c: (c.process_name.lower(), c.remote_addr))
        return result


# ── Firewall manager ───────────────────────────────────────────────────────────

class FirewallManager:
    PREFIX = 'UniTool'

    # Cache of known-blocked keys so context menu state updates instantly
    # without a subprocess round-trip.  Format: 'ip:{addr}' | 'proc:{name}'
    _blocked: set[str] = set()

    # ── Admin / root check ────────────────────────────────────────────────────

    @classmethod
    def is_admin(cls) -> bool:
        if _OS == 'Windows':
            try:
                import ctypes
                return bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception:
                return False
        else:
            return os.geteuid() == 0

    # ── Windows elevation (netsh + UAC) ───────────────────────────────────────

    @classmethod
    def _run_elevated(cls,
                      *netsh_arg_lists: list[str],
                      extra_lines: list[str] = (),
                      force_success: bool = False) -> tuple[bool, str]:
        """Windows: run netsh commands in one elevated cmd.exe — one UAC prompt.

        extra_lines are appended verbatim (e.g. PowerShell kill one-liners).
        force_success=True makes delete-rule failures non-fatal (rule not found).
        """
        if cls.is_admin():
            ok, err = True, ''
            for args in netsh_arg_lists:
                r = subprocess.run(
                    ['netsh'] + list(args),
                    capture_output=True, creationflags=_CNW,
                )
                if r.returncode != 0 and not force_success:
                    ok, err = False, r.stderr.decode(errors='replace').strip()
            for line in extra_lines:
                try:
                    subprocess.run(line, shell=True, capture_output=True,
                                   creationflags=_CNW, timeout=10)
                except Exception:
                    pass
            return ok, err

        import ctypes
        import ctypes.wintypes

        lines = ['@echo off', 'set RESULT=0']
        for args in netsh_arg_lists:
            lines.append('netsh ' + subprocess.list2cmdline(list(args)))
            if not force_success:
                lines.append('if %errorlevel% neq 0 set RESULT=%errorlevel%')
        lines.extend(extra_lines)
        lines.append('exit /b %RESULT%')

        fd, bat_path = tempfile.mkstemp(suffix='.bat', prefix='unitool_fw_')
        try:
            os.write(fd, '\r\n'.join(lines).encode('cp1252', errors='replace'))
            os.close(fd)

            class _SEI(ctypes.Structure):
                _fields_ = [
                    ('cbSize',       ctypes.wintypes.DWORD),
                    ('fMask',        ctypes.c_ulong),
                    ('hwnd',         ctypes.wintypes.HWND),
                    ('lpVerb',       ctypes.c_wchar_p),
                    ('lpFile',       ctypes.c_wchar_p),
                    ('lpParameters', ctypes.c_wchar_p),
                    ('lpDirectory',  ctypes.c_wchar_p),
                    ('nShow',        ctypes.c_int),
                    ('hInstApp',     ctypes.wintypes.HINSTANCE),
                    ('lpIDList',     ctypes.c_void_p),
                    ('lpClass',      ctypes.c_wchar_p),
                    ('hkeyClass',    ctypes.wintypes.HKEY),
                    ('dwHotKey',     ctypes.wintypes.DWORD),
                    ('hIcon',        ctypes.wintypes.HANDLE),
                    ('hProcess',     ctypes.wintypes.HANDLE),
                ]

            sei              = _SEI()
            sei.cbSize       = ctypes.sizeof(sei)
            sei.fMask        = 0x40       # SEE_MASK_NOCLOSEPROCESS
            sei.lpVerb       = 'runas'
            sei.lpFile       = 'cmd.exe'
            sei.lpParameters = f'/C "{bat_path}"'
            sei.nShow        = 0          # SW_HIDE

            if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
                return False, 'UAC elevation was cancelled or denied.'

            ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)

            rc = ctypes.wintypes.DWORD()
            ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(rc))
            ctypes.windll.kernel32.CloseHandle(sei.hProcess)

            return rc.value == 0, ('' if rc.value == 0 else f'netsh exited with code {rc.value}')

        finally:
            try:
                os.unlink(bat_path)
            except OSError:
                pass

    # ── Unix elevation (macOS osascript / Linux pkexec) ───────────────────────

    @classmethod
    def _run_unix(cls, script: str) -> tuple[bool, str]:
        """macOS / Linux: run a bash script, elevating to root if needed.

        Prompts for the password through a Qt dialog when no cached / NOPASSWD
        sudo credential is available, so the app never has to be launched with
        sudo itself.
        """
        from . import elevation
        return elevation.run_script(
            script,
            prompt='UniTool needs administrator access to update the firewall '
                   'and routing table.',
        )

    # ── Public API ────────────────────────────────────────────────────────────

    @classmethod
    def block_ip(cls, ip: str) -> tuple[bool, str]:
        if _OS == 'Windows':
            # Two-layer block: Windows Firewall + null route.
            # Null route works at the routing table, below ESET/AV WFP hooks.
            ok, err = cls._run_elevated(
                ['advfirewall', 'firewall', 'add', 'rule',
                 f'name={cls.PREFIX}-IP-{ip}-in',  'dir=in',
                 'action=block', 'enable=yes', 'profile=any',
                 'protocol=any', f'remoteip={ip}'],
                ['advfirewall', 'firewall', 'add', 'rule',
                 f'name={cls.PREFIX}-IP-{ip}-out', 'dir=out',
                 'action=block', 'enable=yes', 'profile=any',
                 'protocol=any', f'remoteip={ip}'],
                extra_lines=[
                    f'route delete {ip} MASK 255.255.255.255 >nul 2>&1',
                    f'route add    {ip} MASK 255.255.255.255 0.0.0.0 -p',
                    (
                        'powershell -NonInteractive -Command "'
                        f'Get-NetTCPConnection -RemoteAddress {ip} -ErrorAction SilentlyContinue'
                        ' | Remove-NetTCPConnection -ErrorAction SilentlyContinue"'
                    ),
                ],
            )
        elif _OS == 'Darwin':
            # Null-route to loopback + kill existing sessions.
            ok, err = cls._run_unix(
                f'route add -host {ip} 127.0.0.1 2>/dev/null || true\n'
                f'pids=$(lsof -ti TCP@{ip} 2>/dev/null); [ -n "$pids" ] && kill -9 $pids; true\n'
            )
        else:  # Linux
            ok, err = cls._run_unix(
                f'ip route replace blackhole {ip}/32\n'
                f'ss -K dst {ip} 2>/dev/null; true\n'
            )
        if ok:
            cls._blocked.add(f'ip:{ip}')
        return ok, err

    @classmethod
    def unblock_ip(cls, ip: str) -> tuple[bool, str]:
        if _OS == 'Windows':
            ok, err = cls._run_elevated(
                ['advfirewall', 'firewall', 'delete', 'rule',
                 f'name={cls.PREFIX}-IP-{ip}-in'],
                ['advfirewall', 'firewall', 'delete', 'rule',
                 f'name={cls.PREFIX}-IP-{ip}-out'],
                extra_lines=[f'route delete {ip} MASK 255.255.255.255 >nul 2>&1'],
                force_success=True,
            )
        elif _OS == 'Darwin':
            ok, err = cls._run_unix(f'route delete -host {ip} 2>/dev/null; true\n')
        else:  # Linux
            ok, err = cls._run_unix(f'ip route del blackhole {ip}/32 2>/dev/null; true\n')
        if ok:
            cls._blocked.discard(f'ip:{ip}')
        return ok, err

    @classmethod
    def is_ip_blocked(cls, ip: str) -> bool:
        key = f'ip:{ip}'
        if key in cls._blocked:
            return True
        if _OS == 'Windows':
            r = subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'show', 'rule',
                 f'name={cls.PREFIX}-IP-{ip}-in'],
                capture_output=True, creationflags=_CNW,
            )
            found = r.returncode == 0
        elif _OS == 'Darwin':
            r = subprocess.run(['route', 'get', ip], capture_output=True)
            found = b'127.0.0.1' in r.stdout
        else:  # Linux
            r = subprocess.run(['ip', 'route', 'show', f'{ip}/32'], capture_output=True)
            found = b'blackhole' in r.stdout
        if found:
            cls._blocked.add(key)
        return found

    @classmethod
    def block_process(cls, exe_path: str, name: str) -> tuple[bool, str]:
        if _OS == 'Windows':
            kill = (
                'powershell -NonInteractive -Command "'
                f'$pids = (Get-Process | Where-Object {{$_.Path -eq \'{exe_path}\'}} | Select-Object -ExpandProperty Id);'
                'if ($pids) {'
                ' Get-NetTCPConnection -OwningProcess $pids -ErrorAction SilentlyContinue'
                ' | Remove-NetTCPConnection -ErrorAction SilentlyContinue }'
                '"'
            )
            ok, err = cls._run_elevated(
                ['advfirewall', 'firewall', 'add', 'rule',
                 f'name={cls.PREFIX}-{name}-in',  'dir=in',
                 'action=block', 'enable=yes', 'profile=any',
                 'protocol=any', f'program={exe_path}'],
                ['advfirewall', 'firewall', 'add', 'rule',
                 f'name={cls.PREFIX}-{name}-out', 'dir=out',
                 'action=block', 'enable=yes', 'profile=any',
                 'protocol=any', f'program={exe_path}'],
                extra_lines=[kill],
            )
        elif _OS == 'Darwin':
            sff  = '/usr/libexec/ApplicationFirewall/socketfilterfw'
            bname = os.path.basename(exe_path)
            ok, err = cls._run_unix(
                f'{sff} --add "{exe_path}"\n'
                f'{sff} --blockapp "{exe_path}"\n'
                # kill existing connections owned by this process
                f'pids=$(lsof -c "{bname}" -t 2>/dev/null); [ -n "$pids" ] && kill -9 $pids; true\n'
            )
        else:  # Linux — per-process blocking requires iptables cgroup/owner match
            return False, 'Process blocking is not supported on Linux.\nUse IP blocking instead.'
        if ok:
            cls._blocked.add(f'proc:{name}')
        return ok, err

    @classmethod
    def unblock_process(cls, name: str, exe_path: str = '') -> tuple[bool, str]:
        if _OS == 'Windows':
            ok, err = cls._run_elevated(
                ['advfirewall', 'firewall', 'delete', 'rule',
                 f'name={cls.PREFIX}-{name}-in'],
                ['advfirewall', 'firewall', 'delete', 'rule',
                 f'name={cls.PREFIX}-{name}-out'],
                force_success=True,
            )
        elif _OS == 'Darwin':
            if not exe_path:
                return False, 'exe_path is required to unblock a process on macOS.'
            sff = '/usr/libexec/ApplicationFirewall/socketfilterfw'
            ok, err = cls._run_unix(
                f'{sff} --unblockapp "{exe_path}" 2>/dev/null; true\n'
                f'{sff} --remove      "{exe_path}" 2>/dev/null; true\n'
            )
        else:  # Linux — nothing was blocked at process level
            ok, err = True, ''
        if ok:
            cls._blocked.discard(f'proc:{name}')
        return ok, err

    @classmethod
    def is_process_blocked(cls, name: str, exe_path: str = '') -> bool:
        key = f'proc:{name}'
        if key in cls._blocked:
            return True
        if _OS == 'Windows':
            r = subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'show', 'rule',
                 f'name={cls.PREFIX}-{name}-in'],
                capture_output=True, creationflags=_CNW,
            )
            found = r.returncode == 0
        elif _OS == 'Darwin' and exe_path:
            sff = '/usr/libexec/ApplicationFirewall/socketfilterfw'
            r = subprocess.run([sff, '--getappblocked', exe_path], capture_output=True)
            found = b'BLOCK' in r.stdout.upper()
        else:
            found = False
        if found:
            cls._blocked.add(key)
        return found

    @classmethod
    def list_rules(cls) -> list[str]:
        if _OS == 'Windows':
            r = subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'show', 'rule',
                 f'name={cls.PREFIX}*'],
                capture_output=True, text=True, creationflags=_CNW,
            )
            return [
                line.split(':', 1)[1].strip()
                for line in r.stdout.splitlines()
                if line.startswith('Rule Name:')
            ]
        else:
            return sorted(cls._blocked)

    @classmethod
    def remove_rule(cls, rule_name: str) -> bool:
        if _OS == 'Windows':
            ok, _ = cls._run_elevated(
                ['advfirewall', 'firewall', 'delete', 'rule', f'name={rule_name}'],
                force_success=True,
            )
            return ok
        else:
            # Non-Windows rule names are the cache keys ('ip:{addr}' / 'proc:{name}')
            if rule_name.startswith('ip:'):
                ok, _ = cls.unblock_ip(rule_name[3:])
            elif rule_name.startswith('proc:'):
                ok, _ = cls.unblock_process(rule_name[5:])
            else:
                ok = False
            return ok
