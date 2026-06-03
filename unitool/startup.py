import os
import sys
import subprocess
import plistlib
from dataclasses import dataclass, field


@dataclass
class StartupEntry:
    name: str           # Display name
    exe_path: str       # Path to the executable (resolved from command)
    args: str           # Extra arguments
    command: str        # Full command line (raw)
    source: str         # 'HKCU_Run' | 'HKLM_Run' | 'HKCU_RunOnce' | 'HKLM_RunOnce'
                        # | 'StartupFolder_User' | 'StartupFolder_All'
                        # | 'LaunchAgent' | 'Autostart'
    reg_hive: int       # winreg hive constant (Windows only, 0 otherwise)
    reg_key: str        # Registry key path (Windows only)
    reg_name: str       # Registry value name (Windows only)
    folder_path: str    # .lnk / .desktop / .plist file path (folder-based entries)
    enabled: bool
    publisher: str      # From file version info
    file_description: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_file_info(path: str) -> tuple[str, str]:
    """Returns (publisher, description). Uses win32api or returns ('', '')."""
    if not path or not os.path.isfile(path):
        return '', ''
    try:
        import win32api
        try:
            info = win32api.GetFileVersionInfo(path, r'\StringFileInfo\040904b0')
            publisher = info.get('CompanyName', '') or ''
            desc = info.get('FileDescription', '') or ''
            return publisher, desc
        except Exception:
            pass
        try:
            info = win32api.GetFileVersionInfo(path, r'\StringFileInfo\000004b0')
            return info.get('CompanyName', '') or '', info.get('FileDescription', '') or ''
        except Exception:
            return '', ''
    except ImportError:
        return '', ''


def _parse_exe_from_command(command: str) -> tuple[str, str]:
    """
    Split a raw command string into (exe_path, args).
    Handles quoted paths like "C:\\Program Files\\foo.exe" /arg
    and unquoted paths.
    """
    command = command.strip()
    if not command:
        return '', ''

    if command.startswith('"'):
        end = command.find('"', 1)
        if end != -1:
            exe = command[1:end]
            args = command[end + 1:].strip()
            return exe, args
        return command, ''

    parts = command.split(None, 1)
    return parts[0], (parts[1] if len(parts) > 1 else '')


# ── Windows ───────────────────────────────────────────────────────────────────

def _win_approval_status(hive, approved_key: str, value_name: str) -> bool:
    """
    Returns True (enabled) or False (disabled) by inspecting
    HKCU\\...\\Explorer\\StartupApproved\\Run.
    If the key/value does not exist, the entry is considered enabled.
    """
    if sys.platform != 'win32':
        return True
    import winreg
    try:
        with winreg.OpenKey(hive, approved_key) as k:
            data, _ = winreg.QueryValueEx(k, value_name)
            if isinstance(data, (bytes, bytearray)) and len(data) > 0:
                return data[0] != 0x03   # 0x03 = disabled, 0x02 = enabled
    except OSError:
        pass
    return True


def _win_read_run_key(hive, hive_const: int, key_path: str,
                      source: str, approved_key: str) -> list[StartupEntry]:
    if sys.platform != 'win32':
        return []
    import winreg
    entries: list[StartupEntry] = []
    try:
        with winreg.OpenKey(hive, key_path,
                            access=winreg.KEY_READ) as k:
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(k, i)
                    i += 1
                    command = str(data)
                    exe, args = _parse_exe_from_command(command)
                    enabled = _win_approval_status(hive, approved_key, name)
                    publisher, desc = _get_file_info(exe)
                    entries.append(StartupEntry(
                        name=name,
                        exe_path=exe,
                        args=args,
                        command=command,
                        source=source,
                        reg_hive=hive_const,
                        reg_key=key_path,
                        reg_name=name,
                        folder_path='',
                        enabled=enabled,
                        publisher=publisher,
                        file_description=desc,
                    ))
                except OSError:
                    break
    except OSError:
        pass
    return entries


def _parse_lnk_binary(lnk_path: str) -> str:
    """Parse a .lnk file (MS-SHLLINK spec) and extract the local target path."""
    import struct
    try:
        with open(lnk_path, 'rb') as fh:
            data = fh.read(8192)
        if len(data) < 76 or struct.unpack_from('<I', data, 0)[0] != 0x4C:
            return ''
        _CLSID = bytes([0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
                        0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46])
        if data[4:20] != _CLSID:
            return ''
        flags = struct.unpack_from('<I', data, 20)[0]
        pos = 76
        if flags & 0x01:  # HasLinkTargetIDList
            if pos + 2 > len(data):
                return ''
            pos += 2 + struct.unpack_from('<H', data, pos)[0]
        if flags & 0x02:  # HasLinkInfo
            if pos + 28 > len(data):
                return ''
            li_size  = struct.unpack_from('<I', data, pos)[0]
            li_flags = struct.unpack_from('<I', data, pos + 8)[0]
            if li_flags & 0x01:  # VolumeIDAndLocalBasePath
                if li_size >= 0x24:  # Unicode offsets present
                    u_off = struct.unpack_from('<I', data, pos + 28)[0]
                    if u_off:
                        a = pos + u_off
                        e = a
                        while e + 1 < len(data) and data[e:e + 2] != b'\x00\x00':
                            e += 2
                        return data[a:e].decode('utf-16-le', errors='replace')
                a_off = struct.unpack_from('<I', data, pos + 16)[0]
                a = pos + a_off
                e = a
                while e < len(data) and data[e]:
                    e += 1
                return data[a:e].decode('mbcs', errors='replace')
    except Exception:
        pass
    return ''


def _win_lnk_target(lnk_path: str) -> str:
    """Resolve a .lnk shortcut target — no subprocess spawned."""
    # 1. win32com in-process COM (pywin32) — fastest, zero overhead
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            shell  = win32com.client.Dispatch('WScript.Shell')
            target = shell.CreateShortcut(lnk_path).TargetPath
            return target or ''
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        pass
    # 2. Binary .lnk parser — pure Python, no dependencies
    target = _parse_lnk_binary(lnk_path)
    if target:
        return target
    # 3. Last-resort PowerShell — invisible window, no flash
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-WindowStyle', 'Hidden',
             '-NonInteractive', '-Command',
             f'(New-Object -ComObject WScript.Shell).CreateShortcut("{lnk_path}").TargetPath'],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        return r.stdout.strip()
    except Exception:
        return ''


def _win_read_startup_folder(folder: str, source: str) -> list[StartupEntry]:
    if not os.path.isdir(folder):
        return []
    entries: list[StartupEntry] = []
    try:
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            lower = fname.lower()
            if lower.endswith('.lnk'):
                target = _win_lnk_target(fpath)
                exe, args = _parse_exe_from_command(target) if target else (target, '')
                publisher, desc = _get_file_info(exe)
                name = os.path.splitext(fname)[0]
                entries.append(StartupEntry(
                    name=name,
                    exe_path=exe,
                    args=args,
                    command=target,
                    source=source,
                    reg_hive=0,
                    reg_key='',
                    reg_name='',
                    folder_path=fpath,
                    enabled=True,
                    publisher=publisher,
                    file_description=desc,
                ))
            elif lower.endswith('.lnk.disabled'):
                name = fname[: -len('.disabled')]
                name = os.path.splitext(name)[0]
                entries.append(StartupEntry(
                    name=name,
                    exe_path='',
                    args='',
                    command='',
                    source=source,
                    reg_hive=0,
                    reg_key='',
                    reg_name='',
                    folder_path=fpath,
                    enabled=False,
                    publisher='',
                    file_description='',
                ))
    except OSError:
        pass
    return entries


def _get_startup_entries_windows() -> list[StartupEntry]:
    import winreg

    run_key     = r'Software\Microsoft\Windows\CurrentVersion\Run'
    runonce_key = r'Software\Microsoft\Windows\CurrentVersion\RunOnce'

    approved_hkcu_run     = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
    approved_hklm_run     = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
    approved_hkcu_runonce = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\RunOnce'
    approved_hklm_runonce = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\RunOnce'

    entries: list[StartupEntry] = []

    # Registry Run / RunOnce keys
    entries += _win_read_run_key(
        winreg.HKEY_CURRENT_USER, winreg.HKEY_CURRENT_USER,
        run_key, 'HKCU_Run', approved_hkcu_run,
    )
    entries += _win_read_run_key(
        winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_LOCAL_MACHINE,
        run_key, 'HKLM_Run', approved_hklm_run,
    )
    entries += _win_read_run_key(
        winreg.HKEY_CURRENT_USER, winreg.HKEY_CURRENT_USER,
        runonce_key, 'HKCU_RunOnce', approved_hkcu_runonce,
    )
    entries += _win_read_run_key(
        winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_LOCAL_MACHINE,
        runonce_key, 'HKLM_RunOnce', approved_hklm_runonce,
    )

    # Startup folders
    user_startup = os.path.join(
        os.environ.get('APPDATA', ''),
        r'Microsoft\Windows\Start Menu\Programs\Startup',
    )
    all_startup = os.path.join(
        os.environ.get('ALLUSERSPROFILE', ''),
        r'Microsoft\Windows\Start Menu\Programs\Startup',
    )
    entries += _win_read_startup_folder(user_startup, 'StartupFolder_User')
    entries += _win_read_startup_folder(all_startup,  'StartupFolder_All')

    return entries


# ── macOS ─────────────────────────────────────────────────────────────────────

def _get_startup_entries_macos() -> list[StartupEntry]:
    home = os.path.expanduser('~')
    dirs = [
        (os.path.join(home, 'Library', 'LaunchAgents'), 'LaunchAgent'),
        ('/Library/LaunchAgents', 'LaunchAgent'),
    ]
    entries: list[StartupEntry] = []
    for folder, source in dirs:
        if not os.path.isdir(folder):
            continue
        try:
            for fname in os.listdir(folder):
                if not fname.endswith('.plist'):
                    continue
                fpath = os.path.join(folder, fname)
                try:
                    with open(fpath, 'rb') as f:
                        pl = plistlib.load(f)
                    label = pl.get('Label', os.path.splitext(fname)[0])
                    program = pl.get('Program', '')
                    prog_args = pl.get('ProgramArguments', [])
                    if not program and prog_args:
                        program = prog_args[0] if prog_args else ''
                        args_list = prog_args[1:] if len(prog_args) > 1 else []
                    else:
                        args_list = prog_args[1:] if len(prog_args) > 1 else []
                    command = ' '.join(prog_args) if prog_args else program
                    args_str = ' '.join(args_list)
                    disabled = bool(pl.get('Disabled', False))
                    entries.append(StartupEntry(
                        name=label,
                        exe_path=program,
                        args=args_str,
                        command=command,
                        source=source,
                        reg_hive=0,
                        reg_key='',
                        reg_name='',
                        folder_path=fpath,
                        enabled=not disabled,
                        publisher='',
                        file_description='',
                    ))
                except Exception:
                    pass
        except OSError:
            pass
    return entries


# ── Linux ─────────────────────────────────────────────────────────────────────

def _parse_desktop_file(path: str) -> dict[str, str]:
    """Very minimal .desktop parser — returns key/value pairs from [Desktop Entry]."""
    result: dict[str, str] = {}
    in_section = False
    try:
        with open(path, 'r', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line == '[Desktop Entry]':
                    in_section = True
                    continue
                if line.startswith('[') and in_section:
                    break  # left the relevant section
                if in_section and '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    result[k.strip()] = v.strip()
    except OSError:
        pass
    return result


def _get_startup_entries_linux() -> list[StartupEntry]:
    autostart_dir = os.path.join(
        os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')),
        'autostart',
    )
    entries: list[StartupEntry] = []
    if not os.path.isdir(autostart_dir):
        return entries
    try:
        for fname in os.listdir(autostart_dir):
            if not fname.endswith('.desktop'):
                continue
            fpath = os.path.join(autostart_dir, fname)
            d = _parse_desktop_file(fpath)
            name = d.get('Name', os.path.splitext(fname)[0])
            command = d.get('Exec', '')
            exe, args = _parse_exe_from_command(command)
            # Strip %U / %F / %f argument placeholders
            if exe.startswith('%'):
                exe, args = '', ''
            enabled_str = d.get('X-GNOME-Autostart-enabled', 'true').lower()
            enabled = enabled_str not in ('false', '0', 'no')
            entries.append(StartupEntry(
                name=name,
                exe_path=exe,
                args=args,
                command=command,
                source='Autostart',
                reg_hive=0,
                reg_key='',
                reg_name='',
                folder_path=fpath,
                enabled=enabled,
                publisher='',
                file_description='',
            ))
    except OSError:
        pass
    return entries


# ── Public API ────────────────────────────────────────────────────────────────

def get_startup_entries() -> list[StartupEntry]:
    """Return all startup entries for the current platform."""
    try:
        if sys.platform == 'win32':
            return _get_startup_entries_windows()
        elif sys.platform == 'darwin':
            return _get_startup_entries_macos()
        else:
            return _get_startup_entries_linux()
    except Exception:
        return []


def enable_entry(entry: StartupEntry) -> tuple[bool, str]:
    """Enable a startup entry.  Returns (success, error_message)."""
    try:
        if sys.platform == 'win32':
            return _win_enable(entry)
        elif sys.platform == 'darwin':
            return _plist_set_disabled(entry, False)
        else:
            return _desktop_set_enabled(entry, True)
    except Exception as e:
        return False, str(e)


def disable_entry(entry: StartupEntry) -> tuple[bool, str]:
    """Disable a startup entry.  Returns (success, error_message)."""
    try:
        if sys.platform == 'win32':
            return _win_disable(entry)
        elif sys.platform == 'darwin':
            return _plist_set_disabled(entry, True)
        else:
            return _desktop_set_enabled(entry, False)
    except Exception as e:
        return False, str(e)


def delete_entry(entry: StartupEntry) -> tuple[bool, str]:
    """Permanently delete a startup entry.  Returns (success, error_message)."""
    try:
        if sys.platform == 'win32':
            return _win_delete(entry)
        else:
            return _file_delete(entry)
    except Exception as e:
        return False, str(e)


def open_entry_location(entry: StartupEntry):
    """Open the folder containing the executable or shortcut file."""
    from .platform_utils import open_folder
    target = entry.folder_path or entry.exe_path
    if target:
        open_folder(target)


# ── Windows enable / disable / delete ────────────────────────────────────────

_ENABLED_BYTES  = bytes([0x02, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
_DISABLED_BYTES = bytes([0x03, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])


def _approved_key_for(source: str) -> str:
    base = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved'
    if source in ('HKCU_RunOnce', 'HKLM_RunOnce'):
        return base + r'\RunOnce'
    return base + r'\Run'


def _win_enable(entry: StartupEntry) -> tuple[bool, str]:
    import winreg

    if entry.folder_path:
        # Folder-based: rename .lnk.disabled back to .lnk
        if entry.folder_path.lower().endswith('.disabled'):
            new_path = entry.folder_path[:-len('.disabled')]
            os.rename(entry.folder_path, new_path)
            return True, ''
        return True, ''  # already enabled (no .disabled suffix)

    # Registry: set StartupApproved value
    hive = entry.reg_hive
    if not hive:
        return False, 'No registry hive stored for this entry.'
    approved_key = _approved_key_for(entry.source)
    try:
        with winreg.OpenKey(hive, approved_key,
                            access=winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, entry.reg_name, 0, winreg.REG_BINARY, _ENABLED_BYTES)
    except FileNotFoundError:
        with winreg.CreateKey(hive, approved_key) as k:
            winreg.SetValueEx(k, entry.reg_name, 0, winreg.REG_BINARY, _ENABLED_BYTES)
    return True, ''


def _win_disable(entry: StartupEntry) -> tuple[bool, str]:
    import winreg

    if entry.folder_path:
        # Folder-based: rename .lnk to .lnk.disabled
        if not entry.folder_path.lower().endswith('.disabled'):
            new_path = entry.folder_path + '.disabled'
            os.rename(entry.folder_path, new_path)
        return True, ''

    hive = entry.reg_hive
    if not hive:
        return False, 'No registry hive stored for this entry.'
    approved_key = _approved_key_for(entry.source)
    try:
        with winreg.OpenKey(hive, approved_key,
                            access=winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, entry.reg_name, 0, winreg.REG_BINARY, _DISABLED_BYTES)
    except FileNotFoundError:
        with winreg.CreateKey(hive, approved_key) as k:
            winreg.SetValueEx(k, entry.reg_name, 0, winreg.REG_BINARY, _DISABLED_BYTES)
    return True, ''


def _win_delete(entry: StartupEntry) -> tuple[bool, str]:
    import winreg

    if entry.folder_path:
        return _file_delete(entry)

    if not entry.reg_hive or not entry.reg_key or not entry.reg_name:
        return False, 'Missing registry information for this entry.'

    try:
        with winreg.OpenKey(entry.reg_hive, entry.reg_key,
                            access=winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, entry.reg_name)
    except FileNotFoundError:
        return False, f'Registry value "{entry.reg_name}" not found.'
    return True, ''


# ── macOS plist enable / disable ─────────────────────────────────────────────

def _plist_set_disabled(entry: StartupEntry, disabled: bool) -> tuple[bool, str]:
    if not entry.folder_path or not os.path.isfile(entry.folder_path):
        return False, f'Plist file not found: {entry.folder_path}'
    try:
        with open(entry.folder_path, 'rb') as f:
            pl = plistlib.load(f)
        pl['Disabled'] = disabled
        with open(entry.folder_path, 'wb') as f:
            plistlib.dump(pl, f)
        return True, ''
    except Exception as e:
        return False, str(e)


# ── Linux desktop file enable / disable ──────────────────────────────────────

def _desktop_set_enabled(entry: StartupEntry, enabled: bool) -> tuple[bool, str]:
    if not entry.folder_path or not os.path.isfile(entry.folder_path):
        return False, f'Desktop file not found: {entry.folder_path}'
    try:
        with open(entry.folder_path, 'r', errors='replace') as f:
            lines = f.readlines()

        value_str = 'true' if enabled else 'false'
        found = False
        new_lines = []
        in_entry = False
        for line in lines:
            stripped = line.strip()
            if stripped == '[Desktop Entry]':
                in_entry = True
            elif stripped.startswith('[') and in_entry:
                in_entry = False
            if in_entry and stripped.startswith('X-GNOME-Autostart-enabled='):
                new_lines.append(f'X-GNOME-Autostart-enabled={value_str}\n')
                found = True
                continue
            new_lines.append(line)

        if not found:
            # Append the key inside [Desktop Entry] section
            final = []
            in_entry = False
            inserted = False
            for line in new_lines:
                stripped = line.strip()
                if stripped == '[Desktop Entry]':
                    in_entry = True
                elif stripped.startswith('[') and in_entry and not inserted:
                    final.append(f'X-GNOME-Autostart-enabled={value_str}\n')
                    inserted = True
                    in_entry = False
                final.append(line)
            if not inserted:
                final.append(f'X-GNOME-Autostart-enabled={value_str}\n')
            new_lines = final

        with open(entry.folder_path, 'w') as f:
            f.writelines(new_lines)
        return True, ''
    except Exception as e:
        return False, str(e)


# ── Generic file delete ───────────────────────────────────────────────────────

def _file_delete(entry: StartupEntry) -> tuple[bool, str]:
    path = entry.folder_path
    if not path:
        return False, 'No file path stored for this entry.'
    if not os.path.exists(path):
        return False, f'File not found: {path}'
    try:
        os.remove(path)
        return True, ''
    except OSError as e:
        return False, str(e)
