"""
unitool/updater.py
Background version check against GitHub Releases.
Repo: github.com/intelsec/unitool
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

GITHUB_REPO   = 'intelsec/unitool'
RELEASES_URL  = f'https://github.com/{GITHUB_REPO}/releases'
_API_URL      = f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest'
_VERSION_FILE = Path(__file__).parent.parent / 'version.txt'


def current_version() -> str:
    """Read the bundled version from version.txt (e.g. '1.0.0')."""
    try:
        return _VERSION_FILE.read_text().strip().lstrip('v')
    except OSError:
        return '0.0.0'


def _parse(v: str) -> tuple[int, ...]:
    """'v1.2.3' or '1.2.3' → (1, 2, 3). Non-numeric parts become 0."""
    parts = re.sub(r'[^0-9.]', '', v.lstrip('v')).split('.')
    try:
        return tuple(int(p) for p in parts if p)
    except ValueError:
        return (0,)


def _is_newer(remote: str, local: str) -> bool:
    return _parse(remote) > _parse(local)


class UpdateChecker(QThread):
    """
    Fetches the latest release from GitHub in the background.
    Emits update_available(latest_tag, release_url) only when a newer
    version exists.  Silently swallows all network / parse errors.
    """
    update_available = pyqtSignal(str, str)   # latest_tag, html_url

    def run(self):
        try:
            req = urllib.request.Request(
                _API_URL,
                headers={'User-Agent': f'UniTool/{current_version()}'},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())

            tag     = data.get('tag_name', '').strip()
            html    = data.get('html_url', RELEASES_URL).strip()
            prerel  = data.get('prerelease', False)

            if tag and not prerel and _is_newer(tag, current_version()):
                self.update_available.emit(tag.lstrip('v'), html)
        except Exception:
            pass   # network unavailable, rate-limited, etc. — stay silent
