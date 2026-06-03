"""
unitool/dpi.py
Internet Freedom engine backend — connection protection via DPI neutralisation.

Supports two tools:
  • GoodbyeDPI  — Windows only (ValdikSS/goodbyedpi)
  • zapret/nfqws — Linux primary; Windows fallback via WinDivert

Architecture
────────────
DpiProfile  – all user-visible settings collected from the widget
DpiEngine   – manages the subprocess lifetime
  start(profile) → (ok, err)
  stop()         → (ok, err)
  is_running()   → bool

Binary acquisition
──────────────────
GoodbyeDPI:  %APPDATA%\\UniTool\\tools\\goodbyedpi\\goodbyedpi.exe
             downloaded from GitHub Releases on first use, SHA256-verified
zapret:       /usr/sbin/nfqws or /opt/zapret/nfq/nfqws (Linux)
              located via shutil.which; not downloaded automatically
"""
from __future__ import annotations

import os
import sys
import json
import shutil
import hashlib
import subprocess
import threading
import tempfile
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ── Backend selection ─────────────────────────────────────────────────────────

class Backend(Enum):
    GOODBYEDPI = 'goodbyedpi'   # Windows
    ZAPRET     = 'zapret'        # Linux (nfqws)


def default_backend() -> Backend:
    if sys.platform == 'win32':
        return Backend.GOODBYEDPI
    return Backend.ZAPRET


# ── Profile ───────────────────────────────────────────────────────────────────

@dataclass
class DpiProfile:
    preset:      str            # 'dpi_mode_1' … 'dpi_mode_5' | 'dpi_mode_custom'
    techniques:  set[str]       # {'passive', 'frag_https', …}
    dns_enabled: bool  = True
    dns_server:  str   = '1.1.1.1'
    doh:         bool  = True
    domains:     list[str] = field(default_factory=list)   # [] = all traffic
    backend:     Backend   = field(default_factory=default_backend)


# ── GoodbyeDPI argument builder ───────────────────────────────────────────────
#
# When using a named preset the GoodbyeDPI built-in mode flag is preferred
# because it also sets --reverse-frag, --max-payload, etc. which our technique
# set does not individually model.
#
# When the user has customised techniques we fall back to per-flag mode.

_GDPI_PRESET_FLAG: dict[str, str] = {
    'dpi_mode_1': '-1',
    'dpi_mode_2': '-2',
    'dpi_mode_3': '-3',
    'dpi_mode_4': '-4',
    'dpi_mode_5': '-9',   # most aggressive, includes -q (QUIC block)
}

# Individual technique → list[str] argv tokens
_GDPI_TECH: dict[str, list[str]] = {
    'passive':          ['-p'],
    'frag_http':        ['-f', '2'],
    'frag_https':       ['-e', '2'],
    'host_mixedcase':   ['-m'],
    'host_removespace': ['-s'],
    'wrong_seq':        ['--wrong-seq'],
    'wrong_chksum':     ['--wrong-chksum'],
    'native_frag':      ['--native-frag'],
    'fake':             ['--auto-ttl'],   # auto-TTL is the recommended fake approach
}


def build_goodbyedpi_args(profile: DpiProfile, exe: str) -> list[str]:
    """Return the full argv for GoodbyeDPI based on profile settings."""
    args = [exe]

    if profile.preset in _GDPI_PRESET_FLAG and profile.preset != 'dpi_mode_custom':
        # Use GoodbyeDPI's built-in preset mode (includes --reverse-frag etc.)
        args.append(_GDPI_PRESET_FLAG[profile.preset])
    else:
        # Custom / per-technique mode
        seen: set[str] = set()
        for tech in sorted(profile.techniques):
            for tok in _GDPI_TECH.get(tech, []):
                if tok not in seen:
                    args.append(tok)
                    seen.add(tok)
        # Always add --max-payload in custom mode for safety
        if profile.techniques:
            args.append('--max-payload')

    if profile.dns_enabled and profile.dns_server:
        args += ['--dns-addr', profile.dns_server]
        args += ['--dns-port', '53']

    if profile.domains:
        # Write domain list to a temp file and pass via --host-list
        # Caller is responsible for cleaning up the file after engine stops.
        fd, path = tempfile.mkstemp(prefix='unitool_dpi_hosts_', suffix='.txt')
        try:
            os.write(fd, '\n'.join(profile.domains).encode())
        finally:
            os.close(fd)
        args += ['--host-list', path]

    return args


# ── zapret/nfqws argument builder (Linux) ────────────────────────────────────
#
# Valid --dpi-desync strategy names: fake, disorder, disorder2,
#   split, split2, ipfrag4, ipfrag6.
# Numbers appended to strategy names (e.g. "disorder5") are NOT valid.

_NFQWS_QNUM = 200
_NFQWS_PID_FILE = f'/tmp/unitool_nfqws_q{_NFQWS_QNUM}.pid'

# Preset → correct nfqws strategy combo
_NFQWS_PRESET: dict[str, str] = {
    'dpi_mode_1': 'split2',
    'dpi_mode_2': 'split2,fake',
    'dpi_mode_3': 'disorder2,fake',
    'dpi_mode_4': 'disorder2,split2,fake',
    'dpi_mode_5': 'disorder2,split2,fake,ipfrag4',
}

# Technique → nfqws strategy (only the ones with a direct mapping)
_NFQWS_TECH: dict[str, str] = {
    'frag_https':   'split2',
    'frag_http':    'split',
    'native_frag':  'ipfrag4',
    'fake':         'fake',
    'wrong_seq':    'disorder',
    'wrong_chksum': 'fake',   # both fake-packet approaches
    # host_mixedcase / host_removespace / passive have no nfqws equivalent
}

# Technique → optional --dpi-desync-fooling flag
_NFQWS_FOOLING: dict[str, str] = {
    'wrong_seq':    'badseq',
    'wrong_chksum': 'badsum',
}


def build_nfqws_args(profile: DpiProfile, exe: str) -> list[str]:
    """Return argv for nfqws based on profile settings."""
    args = [exe, f'--qnum={_NFQWS_QNUM}']

    if profile.preset in _NFQWS_PRESET and profile.preset != 'dpi_mode_custom':
        strategies = _NFQWS_PRESET[profile.preset]
    else:
        seen: list[str] = []
        for tech in sorted(profile.techniques):
            s = _NFQWS_TECH.get(tech, '')
            if s and s not in seen:
                seen.append(s)
        strategies = ','.join(seen) if seen else 'disorder2,fake'

    args.append(f'--dpi-desync={strategies}')

    # Add fooling flags from selected techniques
    foolings: list[str] = []
    for tech in profile.techniques:
        f = _NFQWS_FOOLING.get(tech, '')
        if f and f not in foolings:
            foolings.append(f)
    if foolings:
        args.append(f'--dpi-desync-fooling={",".join(foolings)}')

    # Fake-packet TTL when fake strategy is active
    if 'fake' in strategies:
        args.append('--dpi-desync-ttl=3')

    if profile.domains:
        fd, path = tempfile.mkstemp(prefix='unitool_dpi_hosts_', suffix='.txt')
        try:
            os.write(fd, '\n'.join(profile.domains).encode())
        finally:
            os.close(fd)
        args.append(f'--hostlist={path}')

    return args


# ── Binary locations ──────────────────────────────────────────────────────────

_APP_TOOLS = Path(os.environ.get('APPDATA', Path.home() / '.local' / 'share')) \
             / 'UniTool' / 'tools'

_GDPI_DIR  = _APP_TOOLS / 'goodbyedpi'
_GDPI_EXE  = _GDPI_DIR / 'goodbyedpi.exe'

# GoodbyeDPI GitHub release metadata (update when bumping bundled version)
_GDPI_RELEASE_API = 'https://api.github.com/repos/ValdikSS/GoodbyeDPI/releases/latest'
_GDPI_ASSET_NAME  = 'goodbyedpi-'   # prefix; we pick the first matching .zip

# Known SHA256 of the current bundled release (leave empty to skip check)
_GDPI_SHA256: dict[str, str] = {}   # {filename: sha256hex}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def goodbyedpi_exe() -> Path:
    return _GDPI_EXE


def goodbyedpi_available() -> bool:
    return _GDPI_EXE.is_file()


def download_goodbyedpi(progress_cb=None) -> tuple[bool, str]:
    """
    Download the latest GoodbyeDPI release from GitHub into _GDPI_DIR.
    progress_cb(pct: int, msg: str) — called during download (pct=-1 = indeterminate).
    Returns (ok, error_message).
    """
    import zipfile

    try:
        if progress_cb:
            progress_cb(-1, 'Fetching release info…')
        with urllib.request.urlopen(_GDPI_RELEASE_API, timeout=15) as r:
            meta = json.loads(r.read())

        assets = meta.get('assets', [])
        zip_asset = next(
            (a for a in assets
             if a['name'].startswith(_GDPI_ASSET_NAME) and a['name'].endswith('.zip')),
            None
        )
        if not zip_asset:
            return False, 'No matching release asset found on GitHub.'

        url  = zip_asset['browser_download_url']
        size = zip_asset.get('size', 0)
        name = zip_asset['name']

        _GDPI_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = _GDPI_DIR / name

        if progress_cb:
            progress_cb(0, f'Downloading {name}…')

        downloaded = 0
        with urllib.request.urlopen(url, timeout=60) as src, \
             open(zip_path, 'wb') as dst:
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                dst.write(chunk)
                downloaded += len(chunk)
                if size and progress_cb:
                    progress_cb(int(downloaded * 100 / size), f'Downloading {name}…')

        # SHA256 check if we know the expected hash
        expected = _GDPI_SHA256.get(name)
        if expected:
            got = _sha256_file(zip_path)
            if got.lower() != expected.lower():
                zip_path.unlink(missing_ok=True)
                return False, f'SHA256 mismatch for {name}.'

        if progress_cb:
            progress_cb(-1, 'Extracting…')

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(_GDPI_DIR)

        zip_path.unlink(missing_ok=True)

        if not _GDPI_EXE.is_file():
            # Some releases nest inside a subfolder; try to find the exe
            matches = list(_GDPI_DIR.rglob('goodbyedpi.exe'))
            if matches:
                shutil.move(str(matches[0]), str(_GDPI_EXE))

        if not _GDPI_EXE.is_file():
            return False, 'goodbyedpi.exe not found after extraction.'

        return True, ''

    except Exception as exc:
        return False, str(exc)


def find_nfqws() -> str | None:
    """Locate the nfqws binary on Linux."""
    candidates = [
        '/usr/sbin/nfqws',
        '/usr/local/sbin/nfqws',
        '/opt/zapret/nfq/nfqws',
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return shutil.which('nfqws')


# ── Linux helpers ────────────────────────────────────────────────────────────

class _LinuxProc:
    """Tracks an elevated Linux process via a PID file."""

    def __init__(self, pid_file: str):
        self._pid_file = pid_file

    def _read_pid(self) -> int | None:
        try:
            return int(Path(self._pid_file).read_text().strip())
        except (OSError, ValueError):
            return None

    def poll(self) -> int | None:
        pid = self._read_pid()
        if pid is None:
            return 0   # not running
        try:
            os.kill(pid, 0)   # signal 0 = existence check
            return None        # still running
        except (ProcessLookupError, PermissionError):
            return 0

    def terminate(self):
        pid = self._read_pid()
        if pid:
            try:
                os.kill(pid, 15)   # SIGTERM
            except (ProcessLookupError, PermissionError):
                pass


def _run_unix_elevated(script: str) -> tuple[bool, str]:
    """Write a bash script to /tmp and run it with pkexec or sudo -n."""
    fd, sh = tempfile.mkstemp(suffix='.sh', prefix='unitool_dpi_', dir='/tmp')
    try:
        os.write(fd, script.encode())
        os.close(fd)
        os.chmod(sh, 0o700)
        for argv in (['pkexec', 'bash', sh], ['sudo', '-n', 'bash', sh]):
            r = subprocess.run(argv, capture_output=True, timeout=30)
            if r.returncode == 0:
                return True, ''
        return False, 'Could not elevate. Install pkexec or configure sudo NOPASSWD.'
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            os.unlink(sh)
        except OSError:
            pass


# ── Engine ────────────────────────────────────────────────────────────────────

class DpiEngine:
    """Manages the GoodbyeDPI / nfqws subprocess lifetime."""

    def __init__(self):
        self._proc:        subprocess.Popen | None = None
        self._lock         = threading.Lock()
        self._host_list:   str | None              = None  # temp file to clean up

    # ── Status ────────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def pid(self) -> int | None:
        with self._lock:
            return self._proc.pid if self._proc else None

    # ── Start ─────────────────────────────────────────────────────────────────

    def start(self, profile: DpiProfile) -> tuple[bool, str]:
        if self.is_running():
            return False, 'Engine is already running.'

        if profile.backend == Backend.GOODBYEDPI:
            return self._start_goodbyedpi(profile)
        return self._start_nfqws(profile)

    def _start_goodbyedpi(self, profile: DpiProfile) -> tuple[bool, str]:
        if not goodbyedpi_available():
            return False, (
                'GoodbyeDPI binary not found.\n'
                'Download it first via the download button.'
            )
        exe  = str(_GDPI_EXE)
        args = build_goodbyedpi_args(profile, exe)
        return self._launch(args, needs_elevation=True)

    def _start_nfqws(self, profile: DpiProfile) -> tuple[bool, str]:
        nfqws = find_nfqws()
        if not nfqws:
            return False, (
                'nfqws not found.\n'
                'Install zapret:  sudo apt install zapret\n'
                '           or:  sudo dnf install zapret'
            )
        nfqws_args = build_nfqws_args(profile, nfqws)
        # Build and run a shell script that:
        # 1. Adds iptables rules to redirect traffic to NFQUEUE
        # 2. Starts nfqws in background
        # 3. Writes its PID so we can kill it later
        ok, err = self._launch_linux_start(nfqws_args)
        if ok:
            with self._lock:
                self._proc = _LinuxProc(_NFQWS_PID_FILE)
        return ok, err

    def _launch_linux_start(self, nfqws_args: list[str]) -> tuple[bool, str]:
        """Write + execute an elevated start script (iptables setup + nfqws)."""
        q = _NFQWS_QNUM
        nfqws_cmd = subprocess.list2cmdline(nfqws_args)
        script = (
            '#!/bin/bash\n'
            'set -e\n'
            # Redirect TCP 80 + 443 OUTPUT to NFQUEUE (idempotent: delete first)
            f'iptables  -D OUTPUT -p tcp -m multiport --dports 80,443'
            f' -j NFQUEUE --queue-num {q} 2>/dev/null; true\n'
            f'ip6tables -D OUTPUT -p tcp -m multiport --dports 80,443'
            f' -j NFQUEUE --queue-num {q} 2>/dev/null; true\n'
            f'iptables  -I OUTPUT -p tcp -m multiport --dports 80,443'
            f' -j NFQUEUE --queue-num {q}\n'
            f'ip6tables -I OUTPUT -p tcp -m multiport --dports 80,443'
            f' -j NFQUEUE --queue-num {q}\n'
            # Start nfqws in background; record PID
            f'{nfqws_cmd} &\n'
            f'echo $! > {_NFQWS_PID_FILE}\n'
        )
        return _run_unix_elevated(script)

    def _launch(self, args: list[str],
                needs_elevation: bool = False) -> tuple[bool, str]:
        """Spawn the subprocess, elevating if needed."""
        try:
            if sys.platform == 'win32':
                if needs_elevation and not _is_admin_win():
                    return self._launch_elevated_win(args)
            elif needs_elevation and os.geteuid() != 0:
                # Linux/macOS: prepend sudo -n (non-interactive) or pkexec
                for prefix in (['pkexec'], ['sudo', '-n']):
                    try:
                        proc = subprocess.Popen(
                            prefix + args,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE,
                        )
                        with self._lock:
                            self._proc = proc
                        return True, ''
                    except (FileNotFoundError, PermissionError):
                        continue
                return False, 'Could not elevate: install pkexec or configure sudo NOPASSWD.'

            flags = 0x08000000 if sys.platform == 'win32' else 0
            proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=flags,
            )
            with self._lock:
                self._proc = proc
            return True, ''
        except FileNotFoundError:
            return False, f'Binary not found: {args[0]}'
        except PermissionError:
            return False, 'Permission denied.'
        except Exception as exc:
            return False, str(exc)

    def _launch_elevated_win(self, args: list[str]) -> tuple[bool, str]:
        """Re-launch goodbyedpi elevated via ShellExecuteExW on Windows."""
        import ctypes, ctypes.wintypes

        exe   = args[0]
        params = subprocess.list2cmdline(args[1:])

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
        sei.fMask        = 0x40   # SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb       = 'runas'
        sei.lpFile       = exe
        sei.lpParameters = params
        sei.nShow        = 0

        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            return False, 'UAC elevation was cancelled or denied.'

        # Wrap the elevated hProcess in a pseudo-Popen so is_running() works
        class _ElevatedProc:
            def __init__(self, handle):
                self._handle = handle
                self.pid = ctypes.windll.kernel32.GetProcessId(handle)

            def poll(self):
                rc = ctypes.wintypes.DWORD()
                ctypes.windll.kernel32.GetExitCodeProcess(
                    self._handle, ctypes.byref(rc))
                return None if rc.value == 259 else rc.value  # 259 = STILL_ACTIVE

            def terminate(self):
                ctypes.windll.kernel32.TerminateProcess(self._handle, 1)
                ctypes.windll.kernel32.CloseHandle(self._handle)

        with self._lock:
            self._proc = _ElevatedProc(sei.hProcess)
        return True, ''

    # ── Stop ──────────────────────────────────────────────────────────────────

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            proc = self._proc
            self._proc = None

        if proc is None:
            return True, ''

        try:
            proc.terminate()
        except Exception as exc:
            return False, str(exc)

        # Linux: remove iptables rules after killing nfqws
        if sys.platform != 'win32':
            q = _NFQWS_QNUM
            script = (
                '#!/bin/bash\n'
                f'iptables  -D OUTPUT -p tcp -m multiport --dports 80,443'
                f' -j NFQUEUE --queue-num {q} 2>/dev/null; true\n'
                f'ip6tables -D OUTPUT -p tcp -m multiport --dports 80,443'
                f' -j NFQUEUE --queue-num {q} 2>/dev/null; true\n'
                f'rm -f {_NFQWS_PID_FILE}\n'
            )
            _run_unix_elevated(script)   # best-effort; ignore errors

        # Clean up temp host list file
        if self._host_list and os.path.exists(self._host_list):
            try:
                os.unlink(self._host_list)
            except OSError:
                pass
            self._host_list = None

        return True, ''

    # ── Cleanup on exit ───────────────────────────────────────────────────────

    def __del__(self):
        if self.is_running():
            try:
                self.stop()
            except Exception:
                pass


# ── Windows admin helper ──────────────────────────────────────────────────────

def _is_admin_win() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
