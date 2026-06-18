"""
unitool/elevation.py
Centralised, on-demand privilege elevation.

UniTool runs as a normal (unprivileged) user.  Individual features that need
root/administrator rights elevate *only the operation that needs it* — the app
itself is never meant to be launched with `sudo`.

Per platform:
  • Windows → ShellExecuteExW(lpVerb='runas')  → native UAC prompt
  • macOS   → osascript 'do shell script … with administrator privileges'
  • Linux   → sudo, prompting for the password through a Qt dialog when no
              cached / NOPASSWD credentials are available.

The Linux path tries, in order:
  1. already root            → run directly
  2. `sudo -n` (cached creds / NOPASSWD) → no prompt
  3. Qt password dialog → `sudo -S`     → prompt once, reuse the cached
     credential for subsequent operations within sudo's timestamp window.

The password is never stored by UniTool: it is handed straight to `sudo` on
stdin and discarded.  All public helpers are safe to call from worker threads —
the password dialog is automatically marshalled onto the GUI thread.
"""
from __future__ import annotations

import os
import sys
import subprocess
import tempfile
import threading


# ── Admin / root detection ──────────────────────────────────────────────────

def is_admin() -> bool:
    """True if the current process already has admin / root privileges."""
    try:
        if sys.platform == 'win32':
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


# Backwards-friendly alias
is_root = is_admin


# ── GUI password prompt (thread-safe) ───────────────────────────────────────

_broker = None
_broker_lock = threading.Lock()
_ask_lock = threading.Lock()   # serialise concurrent prompts


def _get_broker():
    global _broker
    with _broker_lock:
        if _broker is None:
            _broker = _make_broker()
        return _broker


def prompt_password(message: str) -> str | None:
    """Ask the user for their password via a Qt dialog. Returns None if the
    dialog was cancelled or no Qt application is running. Thread-safe."""
    try:
        from PyQt6.QtCore import QCoreApplication
    except Exception:
        return None
    if QCoreApplication.instance() is None:
        return None
    with _ask_lock:
        return _get_broker().ask(message)


def _make_broker():
    """Build the Qt password broker. PyQt6 is imported lazily so this module
    stays importable in headless contexts (build scripts, tests)."""
    from PyQt6.QtCore import QObject, pyqtSignal, QCoreApplication, QThread

    class _PasswordBroker(QObject):
        # str=prompt message — emitted from a worker thread, handled on the GUI thread
        _request = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            app = QCoreApplication.instance()
            if app is not None:
                # Ensure our slot runs on the GUI thread regardless of where
                # this object was first created.
                self.moveToThread(app.thread())
            self._result: str | None = None
            self._event = threading.Event()
            self._request.connect(self._show)   # AutoConnection → queued cross-thread

        def _show(self, message: str):
            try:
                from PyQt6.QtWidgets import QInputDialog, QLineEdit, QApplication
                parent = QApplication.activeWindow()
                pw, ok = QInputDialog.getText(
                    parent,
                    'Administrator authentication required',
                    message,
                    QLineEdit.EchoMode.Password,
                )
                self._result = pw if ok else None
            except Exception:
                self._result = None
            finally:
                self._event.set()

        def ask(self, message: str) -> str | None:
            app = QCoreApplication.instance()
            if app is None:
                return None
            if QThread.currentThread() == app.thread():
                # Already on the GUI thread — show the dialog directly.
                self._show(message)
            else:
                self._event.clear()
                self._request.emit(message)
                self._event.wait()
            return self._result

    return _PasswordBroker()


# ── Elevated execution ──────────────────────────────────────────────────────

_DEFAULT_PROMPT = 'UniTool needs administrator access to apply this change.'


def run_script(script: str, timeout: int = 120,
               prompt: str | None = None) -> tuple[bool, str]:
    """Run a bash script (Unix) as root, returning (ok, error_message).

    On Windows the script is treated as a sequence of shell commands run in an
    elevated cmd.exe via UAC.  Callers that need fine-grained Windows control
    should keep using their own ShellExecute path; this helper exists primarily
    so Unix features stop depending on the whole app being launched with sudo.
    """
    if sys.platform == 'win32':
        return _run_script_win(script, timeout)
    if sys.platform == 'darwin':
        return _run_script_macos(script, timeout)
    return _run_script_linux(script, timeout, prompt or _DEFAULT_PROMPT)


def _err(r: subprocess.CompletedProcess) -> str:
    try:
        return r.stderr.decode(errors='replace').strip()
    except Exception:
        return ''


def _run_script_linux(script: str, timeout: int, prompt: str) -> tuple[bool, str]:
    if os.geteuid() == 0:
        r = subprocess.run(['bash', '-c', script],
                           capture_output=True, timeout=timeout)
        return r.returncode == 0, _err(r)

    fd, sh = tempfile.mkstemp(suffix='.sh', prefix='unitool_elev_', dir='/tmp')
    try:
        os.write(fd, script.encode())
        os.close(fd)
        os.chmod(sh, 0o700)

        # 1. Cached sudo timestamp / NOPASSWD — no prompt needed.
        r = subprocess.run(['sudo', '-n', 'bash', sh],
                           capture_output=True, timeout=timeout)
        if r.returncode == 0:
            return True, ''

        # 2. Interactive: prompt for the password (up to 3 attempts), validating
        #    + refreshing the sudo timestamp with `sudo -v`.
        pw = None
        for attempt in range(3):
            msg = prompt if attempt == 0 else 'Authentication failed — try again:'
            pw = prompt_password(msg)
            if pw is None:
                return False, 'Authentication cancelled.'
            v = subprocess.run(['sudo', '-S', '-p', '', '-v'],
                               input=(pw + '\n').encode(),
                               capture_output=True, timeout=timeout)
            if v.returncode == 0:
                break
        else:
            return False, 'Incorrect password.'

        # 3a. Preferred: run with the now-cached credential (nothing on stdin).
        r = subprocess.run(['sudo', '-n', 'bash', sh],
                           capture_output=True, timeout=timeout)
        if r.returncode == 0:
            return True, ''

        # 3b. Some hardened configs disable timestamp caching
        #     (timestamp_timeout=0) — pipe the password directly for this run.
        r = subprocess.run(['sudo', '-S', '-p', '', 'bash', sh],
                           input=(pw + '\n').encode(),
                           capture_output=True, timeout=timeout)
        return r.returncode == 0, (_err(r) or 'Elevation failed.')
    except subprocess.TimeoutExpired:
        return False, 'Operation timed out.'
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            os.unlink(sh)
        except OSError:
            pass


def _run_script_macos(script: str, timeout: int) -> tuple[bool, str]:
    if os.geteuid() == 0:
        r = subprocess.run(['bash', '-c', script],
                           capture_output=True, timeout=timeout)
        return r.returncode == 0, _err(r)
    fd, sh = tempfile.mkstemp(suffix='.sh', prefix='unitool_elev_', dir='/tmp')
    try:
        os.write(fd, script.encode())
        os.close(fd)
        os.chmod(sh, 0o700)
        # osascript shows the native macOS authentication dialog.
        osa = f'do shell script "bash {sh}" with administrator privileges'
        r = subprocess.run(['osascript', '-e', osa],
                           capture_output=True, timeout=timeout)
        if r.returncode != 0:
            return False, (_err(r) or 'Authorisation cancelled.')
        return True, ''
    except subprocess.TimeoutExpired:
        return False, 'Operation timed out.'
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            os.unlink(sh)
        except OSError:
            pass


def _run_script_win(script: str, timeout: int) -> tuple[bool, str]:
    """Run shell commands in an elevated cmd.exe (one UAC prompt)."""
    import ctypes
    import ctypes.wintypes

    if is_admin():
        r = subprocess.run(['cmd.exe', '/C', script],
                           capture_output=True, creationflags=0x08000000,
                           timeout=timeout)
        return r.returncode == 0, _err(r)

    fd, bat = tempfile.mkstemp(suffix='.bat', prefix='unitool_elev_')
    try:
        os.write(fd, ('@echo off\r\n' + script).encode('cp1252', errors='replace'))
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
        sei.lpParameters = f'/C "{bat}"'
        sei.nShow        = 0          # SW_HIDE

        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            return False, 'UAC elevation was cancelled or denied.'

        ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
        rc = ctypes.wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(rc))
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)
        return rc.value == 0, ('' if rc.value == 0 else f'Script exited with code {rc.value}')
    finally:
        try:
            os.unlink(bat)
        except OSError:
            pass
