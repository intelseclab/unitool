import os
import json
from .platform_utils import data_dir

_CONFIG_DIR  = data_dir('UniTool')
_CONFIG_FILE = os.path.join(_CONFIG_DIR, 'config.json')

_DEFAULTS: dict = {
    'language':             'en',
    'index_folders':        [],
    'index_exclude_hidden': True,
    'index_exclude_system': True,
    'index_min_size_key':   'any',
}


def load() -> dict:
    try:
        with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return {**_DEFAULTS, **json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)


def save(data: dict):
    try:
        os.makedirs(_CONFIG_DIR, exist_ok=True)
        with open(_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass
