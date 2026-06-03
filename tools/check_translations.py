#!/usr/bin/env python3
"""
Validate translation JSON files in resources/languages/.

Uses en.json as the reference and checks every other <code>.json for:
  • valid JSON / UTF-8
  • a non-empty "_name" meta key (shown in the language selector)
  • no missing keys (every en.json key present)
  • no unknown/extra keys (typos, stale keys)
  • matching {placeholders} in each string (prevents runtime format crashes)

Exit code 0 = all good, 1 = problems found. Designed for CI on translation PRs
so contributors only ever need to add/edit a JSON file.
"""
import os
import sys
import json
import string

# Ensure UTF-8 output even on legacy Windows consoles (cp1252).
try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, ValueError):
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
LANG_DIR = os.path.join(HERE, '..', 'resources', 'languages')
REFERENCE = 'en'
META_KEYS = {'_name', '_rtl'}


def _placeholders(text: str) -> set[str]:
    """Return the set of {field} names used in a format string ('{n:,}' -> 'n')."""
    out: set[str] = set()
    try:
        for _lit, field, _spec, _conv in string.Formatter().parse(text):
            if field:
                out.add(field.split('.')[0].split('[')[0])
    except ValueError:
        # Unbalanced braces — report as a sentinel so it surfaces as an error.
        out.add('<malformed>')
    return out


def _load(path: str):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def main() -> int:
    lang_dir = os.path.abspath(LANG_DIR)
    ref_path = os.path.join(lang_dir, f'{REFERENCE}.json')
    if not os.path.isfile(ref_path):
        print(f'ERROR: reference {REFERENCE}.json not found in {lang_dir}')
        return 1

    try:
        ref = _load(ref_path)
    except (json.JSONDecodeError, OSError) as e:
        print(f'ERROR: cannot read {REFERENCE}.json: {e}')
        return 1

    ref_keys = {k for k in ref if k not in META_KEYS}
    ref_ph = {k: _placeholders(str(ref[k])) for k in ref_keys}

    files = sorted(
        f for f in os.listdir(lang_dir)
        if f.endswith('.json') and f != f'{REFERENCE}.json'
    )

    total_problems = 0
    print(f'Reference: {REFERENCE}.json  ({len(ref_keys)} keys)\n')

    for fn in files:
        code = os.path.splitext(fn)[0]
        path = os.path.join(lang_dir, fn)
        problems: list[str] = []

        try:
            data = _load(path)
        except (json.JSONDecodeError, OSError) as e:
            print(f'✗ {fn}: invalid JSON — {e}\n')
            total_problems += 1
            continue

        if not isinstance(data, dict):
            print(f'✗ {fn}: top-level value must be an object\n')
            total_problems += 1
            continue

        # _name meta key
        if not str(data.get('_name', '')).strip():
            problems.append('missing or empty "_name" meta key')

        keys = {k for k in data if k not in META_KEYS}
        missing = ref_keys - keys
        extra = keys - ref_keys

        if missing:
            problems.append(f'{len(missing)} missing key(s): '
                            + ', '.join(sorted(missing)[:15])
                            + (' …' if len(missing) > 15 else ''))
        if extra:
            problems.append(f'{len(extra)} unknown key(s): '
                            + ', '.join(sorted(extra)[:15])
                            + (' …' if len(extra) > 15 else ''))

        # Placeholder mismatches (only for keys that exist in both)
        ph_mismatch = []
        for k in sorted(keys & ref_keys):
            want = ref_ph[k]
            got = _placeholders(str(data[k]))
            if want != got:
                ph_mismatch.append(f'    {k}: expected {sorted(want)}, got {sorted(got)}')
        if ph_mismatch:
            problems.append('placeholder mismatch in '
                            f'{len(ph_mismatch)} key(s):\n' + '\n'.join(ph_mismatch[:20]))

        if problems:
            print(f'✗ {fn} ({data.get("_name", "?")}):')
            for p in problems:
                print(f'  - {p}')
            print()
            total_problems += len(problems)
        else:
            print(f'✓ {fn} ({data.get("_name")})  —  {len(keys)} keys, complete')

    print()
    if total_problems:
        print(f'FAILED: {total_problems} problem(s) found.')
        return 1
    print('All translation files are valid and complete.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
