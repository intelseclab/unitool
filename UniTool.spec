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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)
