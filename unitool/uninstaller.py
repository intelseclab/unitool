import json
import os
import re
import sys
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class InstalledApp:
    name: str
    version: str
    publisher: str
    install_date: str           # 'YYYY-MM-DD' or '' if unknown
    install_location: str       # May be empty
    size_kb: int                # EstimatedSize in KB (may be 0)
    uninstall_string: str
    quiet_uninstall_string: str
    reg_path: str               # Full registry path (Windows) or bundle path (macOS)
    source: str                 # 'HKLM' | 'HKCU' | 'HKLM_WOW' | 'macOS_App' | 'deb' | 'rpm'
    is_system: bool             # True for Windows system components (hide by default)


# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} PB'


def _parse_install_date(raw: str) -> str:
    """Parse '20231201' → '2023-12-01'. Returns '' on any failure."""
    if not raw:
        return ''
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        return f'{raw[:4]}-{raw[4:6]}-{raw[6:]}'
    return ''


_SYSTEM_NAME_WORDS = {
    'visual', 'c++', 'redistributable', '.net', 'runtime',
    'update', 'sdk', 'driver', 'windows',
}


def _is_system_component(name: str, publisher: str) -> bool:
    """Heuristic: mark as system if Microsoft publisher AND name contains system keywords."""
    if 'microsoft' not in publisher.lower():
        return False
    name_lower = name.lower()
    return any(kw in name_lower for kw in _SYSTEM_NAME_WORDS)


# ── Windows registry scan ─────────────────────────────────────────────────────

if sys.platform == 'win32':
    import winreg  # noqa: E402  (import at module level for type checking)

    _UNINSTALL_KEYS = [
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
         'HKLM'),
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
         'HKLM_WOW'),
        (winreg.HKEY_CURRENT_USER,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
         'HKCU'),
    ]


def _read_reg_str(key, name: str) -> str:
    try:
        val, _ = winreg.QueryValueEx(key, name)
        return str(val).strip() if val else ''
    except (OSError, TypeError):
        return ''


def _read_reg_int(key, name: str) -> int:
    try:
        val, _ = winreg.QueryValueEx(key, name)
        return int(val)
    except (OSError, TypeError, ValueError):
        return 0


def _scan_windows() -> list[InstalledApp]:
    apps: list[InstalledApp] = []
    seen: set[str] = set()

    for hive, subkey_path, source in _UNINSTALL_KEYS:
        try:
            hive_key = winreg.OpenKey(hive, subkey_path,
                                      access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except OSError:
            try:
                hive_key = winreg.OpenKey(hive, subkey_path)
            except OSError:
                continue

        try:
            idx = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(hive_key, idx)
                except OSError:
                    break
                idx += 1

                full_path = f'{subkey_path}\\{subkey_name}'
                try:
                    sub = winreg.OpenKey(hive_key, subkey_name,
                                         access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
                except OSError:
                    try:
                        sub = winreg.OpenKey(hive_key, subkey_name)
                    except OSError:
                        continue

                try:
                    system_component = _read_reg_int(sub, 'SystemComponent')
                    if system_component == 1:
                        continue

                    name = _read_reg_str(sub, 'DisplayName')
                    if not name:
                        continue

                    uninstall_str = _read_reg_str(sub, 'UninstallString')
                    if not uninstall_str:
                        continue

                    # Deduplicate by name (prefer HKLM over HKCU)
                    dedup_key = name.lower()
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    version   = _read_reg_str(sub, 'DisplayVersion')
                    publisher = _read_reg_str(sub, 'Publisher')
                    raw_date  = _read_reg_str(sub, 'InstallDate')
                    location  = _read_reg_str(sub, 'InstallLocation')
                    size_kb   = _read_reg_int(sub, 'EstimatedSize')
                    quiet_str = _read_reg_str(sub, 'QuietUninstallString')

                    apps.append(InstalledApp(
                        name=name,
                        version=version,
                        publisher=publisher,
                        install_date=_parse_install_date(raw_date),
                        install_location=location,
                        size_kb=size_kb,
                        uninstall_string=uninstall_str,
                        quiet_uninstall_string=quiet_str,
                        reg_path=full_path,
                        source=source,
                        is_system=_is_system_component(name, publisher),
                    ))
                finally:
                    sub.Close()
        finally:
            hive_key.Close()

    return apps


# ── Windows Store (UWP/MSIX) scan ────────────────────────────────────────────

def _appx_display_name(install_location: str, pkg_name: str) -> str:
    """Read DisplayName from AppxManifest.xml; fall back to cleaned package name."""
    if install_location:
        manifest_path = os.path.join(install_location, 'AppxManifest.xml')
        try:
            tree = ET.parse(manifest_path)
            root = tree.getroot()
            ns_prefix = ''
            if root.tag.startswith('{'):
                ns_prefix = root.tag.split('}')[0] + '}'
            for props in root.iter(f'{ns_prefix}Properties'):
                dn = props.find(f'{ns_prefix}DisplayName')
                if dn is not None and dn.text:
                    name = dn.text.strip()
                    if not name.startswith('ms-resource:'):
                        return name
        except (ET.ParseError, OSError, PermissionError):
            pass

    # Strip publisher prefix (e.g. "Microsoft.WindowsCalculator" → "Windows Calculator")
    parts = pkg_name.split('.', 1)
    base = parts[1] if len(parts) == 2 else pkg_name
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', base).strip() or pkg_name


def _scan_windows_store() -> list['InstalledApp']:
    """Enumerate UWP/MSIX packages via PowerShell Get-AppxPackage."""
    ps_cmd = (
        'Get-AppxPackage | Where-Object {!$_.IsFramework} | '
        'Select-Object Name,Version,PublisherDisplayName,InstallLocation,'
        'PackageFullName,SignatureKind | ConvertTo-Json -Depth 1'
    )
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_cmd],
            capture_output=True, timeout=30,
            creationflags=0x08000000,
        )
        raw = result.stdout.decode('utf-8', errors='replace').strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
    except Exception:
        return []

    apps: list[InstalledApp] = []
    for item in data:
        try:
            pkg_name = (item.get('Name') or '').strip()
            if not pkg_name:
                continue
            pkg_full = (item.get('PackageFullName') or '').strip()
            if not pkg_full:
                continue
            install_loc = item.get('InstallLocation') or ''
            if install_loc is None:
                install_loc = ''

            display_name = _appx_display_name(install_loc, pkg_name)
            version = str(item.get('Version') or '').strip()
            publisher = (item.get('PublisherDisplayName') or '').strip()

            sig_raw = item.get('SignatureKind')
            if isinstance(sig_raw, int):
                is_sys = sig_raw == 4   # 4 = System in Windows SignatureKind enum
            else:
                is_sys = str(sig_raw or '').lower() == 'system'

            apps.append(InstalledApp(
                name=display_name,
                version=version,
                publisher=publisher,
                install_date='',
                install_location=install_loc,
                size_kb=0,
                uninstall_string=f'appx:{pkg_full}',
                quiet_uninstall_string='',
                reg_path=pkg_full,
                source='Store',
                is_system=is_sys,
            ))
        except Exception:
            continue

    return apps


# ── macOS scan ────────────────────────────────────────────────────────────────

def _dir_size_bytes(path: str) -> int:
    """Walk a directory tree and sum file sizes."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path, followlinks=False):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _scan_macos() -> list[InstalledApp]:
    apps: list[InstalledApp] = []
    apps_dir = '/Applications'
    if not os.path.isdir(apps_dir):
        return apps

    for entry in os.scandir(apps_dir):
        if not entry.name.endswith('.app'):
            continue
        bundle_path = entry.path

        # Read Info.plist
        plist_path = os.path.join(bundle_path, 'Contents', 'Info.plist')
        name = entry.name[:-4]          # strip .app as fallback
        version = ''
        publisher = ''

        try:
            import plistlib
            with open(plist_path, 'rb') as fh:
                info = plistlib.load(fh)
            name      = info.get('CFBundleName') or info.get('CFBundleDisplayName') or name
            version   = info.get('CFBundleShortVersionString') or info.get('CFBundleVersion') or ''
            publisher = info.get('CFBundleGetInfoString') or info.get('NSHumanReadableCopyright') or ''
        except Exception:
            pass

        size_bytes = _dir_size_bytes(bundle_path)
        size_kb = size_bytes // 1024

        apps.append(InstalledApp(
            name=name,
            version=version,
            publisher=publisher,
            install_date='',
            install_location=bundle_path,
            size_kb=size_kb,
            uninstall_string='',
            quiet_uninstall_string='',
            reg_path=bundle_path,
            source='macOS_App',
            is_system=False,
        ))

    return apps


# ── Linux scan ────────────────────────────────────────────────────────────────

def _scan_linux_deb() -> list[InstalledApp]:
    apps: list[InstalledApp] = []
    try:
        result = subprocess.run(
            ['dpkg-query', '-W',
             '-f=${Package}\t${Version}\t${Installed-Size}\t${Maintainer}\t${Description}\n'],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return apps
        for line in result.stdout.splitlines():
            parts = line.split('\t', 4)
            if len(parts) < 5:
                continue
            pkg, ver, size_kb_str, maintainer, desc = parts
            pkg = pkg.strip()
            if not pkg:
                continue
            try:
                size_kb = int(size_kb_str.strip())
            except ValueError:
                size_kb = 0
            apps.append(InstalledApp(
                name=pkg,
                version=ver.strip(),
                publisher=maintainer.strip(),
                install_date='',
                install_location='',
                size_kb=size_kb,
                uninstall_string='',
                quiet_uninstall_string='',
                reg_path=pkg,
                source='deb',
                is_system=False,
            ))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return apps


def _scan_linux_rpm() -> list[InstalledApp]:
    apps: list[InstalledApp] = []
    try:
        result = subprocess.run(
            ['rpm', '-qa', '--queryformat',
             '%{NAME}\t%{VERSION}\t%{SIZE}\t%{VENDOR}\t%{SUMMARY}\n'],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return apps
        for line in result.stdout.splitlines():
            parts = line.split('\t', 4)
            if len(parts) < 5:
                continue
            pkg, ver, size_bytes_str, vendor, summary = parts
            pkg = pkg.strip()
            if not pkg:
                continue
            try:
                size_kb = int(size_bytes_str.strip()) // 1024
            except ValueError:
                size_kb = 0
            apps.append(InstalledApp(
                name=pkg,
                version=ver.strip(),
                publisher=vendor.strip(),
                install_date='',
                install_location='',
                size_kb=size_kb,
                uninstall_string='',
                quiet_uninstall_string='',
                reg_path=pkg,
                source='rpm',
                is_system=False,
            ))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return apps


def _scan_linux() -> list[InstalledApp]:
    apps = _scan_linux_deb()
    if not apps:
        apps = _scan_linux_rpm()
    return apps


# ── Public API ────────────────────────────────────────────────────────────────

def get_installed_apps() -> list[InstalledApp]:
    """Return all installed applications, sorted by name (case-insensitive)."""
    try:
        if sys.platform == 'win32':
            apps = _scan_windows()
            # Merge in Store apps; deduplicate by lowercase name against registry apps
            reg_names = {a.name.lower() for a in apps}
            for store_app in _scan_windows_store():
                if store_app.name.lower() not in reg_names:
                    apps.append(store_app)
                    reg_names.add(store_app.name.lower())
        elif sys.platform == 'darwin':
            apps = _scan_macos()
        else:
            apps = _scan_linux()
    except Exception:
        apps = []

    apps.sort(key=lambda a: a.name.lower())
    return apps


def uninstall_app(app: InstalledApp, silent: bool = False) -> tuple[bool, str]:
    """
    Launch the app's uninstaller.
    Returns (True, '') immediately after launching — does not wait.
    """
    if sys.platform == 'win32':
        # ── Microsoft Store / UWP app ──
        if app.source == 'Store' or app.uninstall_string.startswith('appx:'):
            pkg_full = app.uninstall_string.removeprefix('appx:')
            if not pkg_full:
                return False, 'No PackageFullName available'
            try:
                subprocess.Popen(
                    ['powershell', '-NoProfile', '-NonInteractive', '-Command',
                     f"Remove-AppxPackage -Package '{pkg_full}'"],
                    creationflags=0x08000000,
                )
                return True, ''
            except Exception as e:
                return False, str(e)

        # ── Classic Win32 / MSI app ──
        cmd = ''
        if silent and app.quiet_uninstall_string:
            cmd = app.quiet_uninstall_string
        else:
            cmd = app.uninstall_string

        if not cmd:
            return False, 'No uninstall string available'

        # Add silent flags to msiexec if not already present
        if silent and 'msiexec' in cmd.lower():
            if '/quiet' not in cmd.lower() and '/q' not in cmd.lower():
                cmd = cmd.rstrip() + ' /quiet /norestart'

        try:
            subprocess.Popen(cmd, shell=True)
            return True, ''
        except Exception as e:
            return False, str(e)

    elif sys.platform == 'darwin':
        bundle_path = app.reg_path
        if not os.path.exists(bundle_path):
            return False, f'Bundle not found: {bundle_path}'
        try:
            shutil.rmtree(bundle_path)
            return True, ''
        except Exception as e:
            return False, str(e)

    else:
        # Linux
        try:
            if app.source == 'deb':
                proc = subprocess.Popen(
                    ['pkexec', 'apt-get', 'remove', '-y', app.name]
                )
            else:
                proc = subprocess.Popen(
                    ['pkexec', 'dnf', 'remove', '-y', app.name]
                )
            _ = proc  # don't wait
            return True, ''
        except Exception as e:
            return False, str(e)


def find_leftovers(app: InstalledApp) -> list[tuple[str, int]]:
    """
    Search common user data locations for leftover directories belonging to app.
    Returns list of (path, size_bytes) sorted by size descending.
    """
    _SKIP_WORDS = {'the', 'and', 'for', 'from', 'with', 'version', 'edition'}

    search_names: set[str] = set()

    # Words from app name
    cleaned = app.name.replace('(', ' ').replace(')', ' ').replace('-', ' ')
    for word in cleaned.split():
        if len(word) > 3 and word.lower() not in _SKIP_WORDS:
            search_names.add(word)

    # First word of publisher
    if app.publisher:
        first_word = app.publisher.split()[0]
        if first_word:
            search_names.add(first_word)

    # Basename of install location
    if app.install_location:
        base = os.path.basename(app.install_location.rstrip('/\\'))
        if base:
            search_names.add(base)

    if not search_names:
        return []

    # Search directories
    if sys.platform == 'win32':
        search_roots = []
        for env_var in ('APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA'):
            path = os.environ.get(env_var, '')
            if path and os.path.isdir(path):
                search_roots.append(path)
        # Also search parent of install_location
        if app.install_location:
            parent = os.path.dirname(app.install_location.rstrip('/\\'))
            if parent and os.path.isdir(parent) and parent not in search_roots:
                search_roots.append(parent)
    elif sys.platform == 'darwin':
        home = os.path.expanduser('~')
        search_roots = [
            os.path.join(home, 'Library', 'Application Support'),
            os.path.join(home, 'Library', 'Preferences'),
            os.path.join(home, 'Library', 'Caches'),
            os.path.join(home, 'Library', 'Logs'),
        ]
    else:
        home = os.path.expanduser('~')
        search_roots = [
            os.path.join(home, '.config'),
            os.path.join(home, '.local', 'share'),
            os.path.join(home, '.cache'),
        ]
        if app.install_location:
            parent = os.path.dirname(app.install_location.rstrip('/\\'))
            if parent and os.path.isdir(parent):
                search_roots.append(parent)

    install_loc_norm = (
        os.path.normcase(app.install_location.rstrip('/\\'))
        if app.install_location else ''
    )

    found: list[tuple[str, int]] = []
    seen_paths: set[str] = set()

    for root in search_roots:
        if not os.path.isdir(root):
            continue
        try:
            entries = os.scandir(root)
        except (OSError, PermissionError):
            continue
        with entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                entry_name_lower = entry.name.lower()
                matched = any(
                    sn.lower() in entry_name_lower
                    for sn in search_names
                )
                if not matched:
                    continue
                # Exclude the install_location itself
                if install_loc_norm and os.path.normcase(entry.path) == install_loc_norm:
                    continue
                norm_path = os.path.normcase(entry.path)
                if norm_path in seen_paths:
                    continue
                seen_paths.add(norm_path)
                size = _dir_size_bytes(entry.path)
                found.append((entry.path, size))

    found.sort(key=lambda x: x[1], reverse=True)
    return found


def clean_leftovers(paths: list[str]) -> tuple[int, list[str]]:
    """
    Remove each path (directory tree). Returns (cleaned_count, error_list).
    """
    cleaned = 0
    errors: list[str] = []
    for path in paths:
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.isfile(path):
                os.remove(path)
            else:
                errors.append(f'{path}: not found')
                continue
            cleaned += 1
        except Exception as e:
            errors.append(f'{path}: {e}')
    return cleaned, errors
