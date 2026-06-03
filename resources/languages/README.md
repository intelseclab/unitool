# Translations

Every UI string in UniTool lives in a JSON file in this folder — one file per
language, named by its [ISO 639-1 code](https://en.wikipedia.org/wiki/List_of_ISO_639_language_codes)
(`en.json`, `tr.json`, `fa.json`, …). **No code changes are needed to add a language** —
just add a JSON file and open a pull request.

## Add a new language

1. Copy `en.json` to `<code>.json` (e.g. `de.json` for German).
2. Set the two meta keys at the top:
   ```json
   {
     "_name": "Deutsch",   // native name shown in the language selector
     "_rtl": false,        // true only for right-to-left scripts (Arabic, Persian, Hebrew…)
     "app_title": "UniTool",
     ...
   }
   ```
3. Translate every **value**. Never change the **keys** (the left-hand side).
4. Keep every `{placeholder}` exactly as in English — e.g.
   `"{n} of {total} apps"` → `"{n} von {total} Apps"`. Placeholders are filled in
   at runtime; renaming or dropping one will break that string.
5. Leave symbols/emojis (`▶`, `🗑`, `←`) as-is unless they need flipping for RTL.
6. Open a PR. CI (`Validate Translations`) checks your file automatically.

## Rules the CI enforces

- Valid UTF-8 JSON.
- A non-empty `"_name"`.
- All keys from `en.json` are present (no missing strings).
- No unknown/extra keys (catches typos).
- Each string uses the same `{placeholders}` as the English original.

You can run the same check locally:

```bash
python tools/check_translations.py
```

## Partial translations

The app falls back to English for any key not found in the active language, so a
half-finished file still works at runtime — but CI requires completeness before a
PR can merge. Translate every key, or copy the English value for ones you are unsure of.
