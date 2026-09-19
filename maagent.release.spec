# -*- mode: python ; coding: utf-8 -*-
# Release build: bundles a CLEAN default config (no secrets) so the shipped exe
# contains no email authorization code. The installer also drops config.yaml
# next to the exe, which takes priority over this bundled fallback.
from PyInstaller.utils.hooks import collect_all

datas = [
    ('maagent/gui/assets', 'maagent/gui/assets'),
    ('maagent/server/web', 'maagent/server/web'),
    ('installer/bundle/config/config.yaml', 'config'),
]
binaries = []
hiddenimports = ['maagent.control.tailscale', 'maagent.gui.phone']
for _pkg in ('rapidocr_onnxruntime', 'onnxruntime', 'pyclipper', 'shapely'):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h


a = Analysis(
    ['maagent/gui/app.py'],
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
    name='maagent',
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
    uac_admin=True,
    icon=['maagent/gui/assets/icon.ico'],
)
