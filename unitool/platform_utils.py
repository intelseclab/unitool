import os
import sys
import subprocess


def data_dir(app_name: str) -> str:
    """Return the platform-appropriate user data directory."""
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
    return os.path.join(base, app_name)


def open_path(path: str):
    """Open a file or folder with the system default application."""
    path = os.path.normpath(path)
    if sys.platform == 'win32':
        os.startfile(path)
    elif sys.platform == 'darwin':
        subprocess.run(['open', path], check=False)
    else:
        subprocess.run(['xdg-open', path], check=False)


def open_folder(path: str):
    """Open the containing folder, selecting the file where supported."""
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    folder = os.path.normpath(folder)
    if sys.platform == 'win32':
        os.startfile(folder)
    elif sys.platform == 'darwin':
        subprocess.run(['open', folder], check=False)
    else:
        subprocess.run(['xdg-open', folder], check=False)


# Directories to skip during indexing, per platform
_WIN_SKIP = frozenset({
    'windows', '$recycle.bin', 'system volume information',
    'recovery', '$winreinstall', 'msocache', 'boot',
    'programdata', 'perflogs',
})
_LINUX_SKIP = frozenset({
    'proc', 'sys', 'dev', 'run', 'snap', 'boot',
    'lost+found',
})
_MAC_SKIP = frozenset({
    'private', 'cores', 'dev',
})

if sys.platform == 'win32':
    SYSTEM_DIRS = _WIN_SKIP
elif sys.platform == 'darwin':
    SYSTEM_DIRS = _MAC_SKIP
else:
    SYSTEM_DIRS = _LINUX_SKIP
