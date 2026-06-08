# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_all

datas = [
    ('icon.png', '.'),
    ('resources/check.svg', 'resources'),
    ('resources/languages', 'resources/languages'),  # JSON translation files
]
binaries = []
hiddenimports = ['PyQt6.sip', 'send2trash', 'xxhash']
tmp_ret = collect_all('unitool')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# Icon: .ico on Windows, .icns on macOS (if available), skip on Linux
if sys.platform == 'win32':
    _icon = ['icon.ico']
elif sys.platform == 'darwin':
    import os
    _icon = ['icon.icns'] if os.path.exists('icon.icns') else ['icon.png']
else:
    _icon = ['icon.png']


# ── Windows version resource + UPX policy ────────────────────────────────────
# Two things make the Windows build look far less suspicious to SmartScreen and
# antivirus heuristics, with no certificate required:
#   1. Embed a proper version resource (CompanyName / ProductName / …). A blank
#      one is a red flag and feeds AV scoring.
#   2. Do NOT UPX-pack the exe — UPX is a notorious AV false-positive trigger.
# (A real Authenticode signature is still required to remove the "unknown
# publisher" prompt entirely — see .github/workflows/build.yml for the stub.)
_version_file = None
_use_upx = True
if sys.platform == 'win32':
    import os
    _use_upx = False
    try:
        _ver = open('version.txt').read().strip() or '0.0.0'
    except OSError:
        _ver = '0.0.0'
    _parts = (_ver.split('.') + ['0', '0', '0', '0'])[:4]
    _vt = tuple(int(''.join(c for c in p if c.isdigit()) or '0') for p in _parts)
    os.makedirs('build', exist_ok=True)
    _version_file = os.path.join('build', '_version_info.txt')
    with open(_version_file, 'w', encoding='utf-8') as _vf:
        _vf.write(
            "# UTF-8\n"
            "VSVersionInfo(\n"
            "  ffi=FixedFileInfo(\n"
            f"    filevers={_vt}, prodvers={_vt},\n"
            "    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,\n"
            "    date=(0, 0)),\n"
            "  kids=[\n"
            "    StringFileInfo([StringTable('040904B0', [\n"
            "      StringStruct('CompanyName', 'intelseclab'),\n"
            "      StringStruct('FileDescription', 'UniTool — system utility suite'),\n"
            f"      StringStruct('FileVersion', '{_ver}'),\n"
            "      StringStruct('InternalName', 'UniTool'),\n"
            "      StringStruct('OriginalFilename', 'UniTool.exe'),\n"
            "      StringStruct('ProductName', 'UniTool'),\n"
            f"      StringStruct('ProductVersion', '{_ver}'),\n"
            "      StringStruct('LegalCopyright', '\\u00a9 intelseclab')])]),\n"
            "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
            "  ]\n"
            ")\n"
        )


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='UniTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=_use_upx,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
    version=_version_file,
)
