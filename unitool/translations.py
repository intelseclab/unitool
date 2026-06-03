"""
unitool/translations.py
Translation system. All strings live in resources/languages/<code>.json.
Languages are auto-discovered from that folder at import time; English is the
fallback for any missing key or language.
"""
import os
import sys
import json

# ── Locate the languages folder (works from source tree and PyInstaller) ──────

def _languages_dir() -> str:
    # PyInstaller one-file: data is unpacked to sys._MEIPASS
    base = getattr(sys, '_MEIPASS', None)
    candidates = []
    if base:
        candidates.append(os.path.join(base, 'resources', 'languages'))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, '..', 'resources', 'languages'))
    candidates.append(os.path.join(os.getcwd(), 'resources', 'languages'))
    for c in candidates:
        if os.path.isdir(c):
            return os.path.abspath(c)
    return os.path.abspath(candidates[-1])


_LANG_DIR = _languages_dir()

# Fallback display names for language codes whose JSON omits a "_name" meta key.
# A new language is fully self-describing if its JSON sets "_name" / "_rtl";
# this table is only consulted when those meta keys are absent.
_LANG_NAMES: dict[str, str] = {
    'en': 'English',
    'tr': 'Türkçe',
    'fa': 'فارسی',
    'de': 'Deutsch',
    'fr': 'Français',
    'es': 'Español',
    'ru': 'Русский',
    'ar': 'العربية',
}

# Fallback RTL set, used only when a JSON omits the "_rtl" meta key.
_RTL_LANGS = {'fa', 'ar', 'he', 'ur'}

# Meta keys a language JSON may define for self-description.
#   "_name": native display name shown in the language selector
#   "_rtl" : true if the language is written right-to-left
_META_KEYS = ('_name', '_rtl')

_META: dict[str, dict] = {}   # {code: {'name': str, 'rtl': bool}}


# ── Load all language files ───────────────────────────────────────────────────

def _load_all() -> dict[str, dict[str, str]]:
    out:  dict[str, dict[str, str]] = {}
    meta: dict[str, dict] = {}
    try:
        files = sorted(f for f in os.listdir(_LANG_DIR) if f.endswith('.json'))
    except OSError:
        files = []
    for fn in files:
        code = os.path.splitext(fn)[0].lower()
        try:
            with open(os.path.join(_LANG_DIR, fn), encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict) or not data:
                continue
            meta[code] = {
                'name': str(data['_name']) if data.get('_name') else
                        _LANG_NAMES.get(code, code.upper()),
                'rtl':  bool(data['_rtl']) if '_rtl' in data else
                        (code in _RTL_LANGS),
            }
            # Strip meta keys so they never appear as translatable strings.
            out[code] = {str(k): str(v) for k, v in data.items()
                         if k not in _META_KEYS}
        except (OSError, json.JSONDecodeError, ValueError, KeyError):
            continue
    if 'en' not in out:
        out['en'] = {}   # guarantee a fallback table exists
        meta.setdefault('en', {'name': 'English', 'rtl': False})

    global _META
    _META = meta
    return out


_TR: dict[str, dict[str, str]] = _load_all()
_lang = 'en'


# ── Public API ────────────────────────────────────────────────────────────────

def tr(key: str, **kw) -> str:
    """Look up a key in the current language, falling back to English then the
    raw key. Format placeholders are applied when keyword args are given."""
    text = _TR.get(_lang, {}).get(key)
    if text is None:
        text = _TR.get('en', {}).get(key, key)
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def set_language(lang: str) -> None:
    global _lang
    if lang in _TR:
        _lang = lang


def current_language() -> str:
    return _lang


def _name_of(code: str) -> str:
    m = _META.get(code)
    if m and m.get('name'):
        return m['name']
    return _LANG_NAMES.get(code, code.upper())


def available_languages() -> list[tuple[str, str]]:
    """Return [(code, display_name), …] for every loaded language.
    English first, then the rest alphabetically by display name.
    Display name comes from the JSON's "_name" meta key when present."""
    codes = list(_TR.keys())
    codes.sort(key=lambda c: (c != 'en', _name_of(c).lower()))
    return [(c, _name_of(c)) for c in codes]


def is_rtl(lang: str | None = None) -> bool:
    """True if the given (or current) language is written right-to-left.
    Read from the JSON's "_rtl" meta key when present."""
    code = lang or _lang
    m = _META.get(code)
    if m and 'rtl' in m:
        return bool(m['rtl'])
    return code in _RTL_LANGS


def reload_languages() -> None:
    """Re-read all JSON files from disk (useful during development)."""
    global _TR
    _TR = _load_all()
