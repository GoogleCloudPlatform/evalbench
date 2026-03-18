# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import copy_metadata, collect_all

def collect_dynamic_resources():
    datas = []
    binaries = []
    hiddenimports = []
    
    # 1. Project Assets
    # Include README from the repo root
    datas.append(('README.md', '.'))

    # 2. Dependency Metadata / Complex Packages
    packages_to_collect = [
        'google.auth',
        'google.adk',
        'google.genai'
    ]
    
    for pkg in packages_to_collect:
        try:
            tmp_ret = collect_all(pkg)
            datas += tmp_ret[0]
            binaries += tmp_ret[1]
            hiddenimports += tmp_ret[2]
        except Exception as e:
            print(f'Failed to collect_all for {pkg}: {e}')
    
    packages_needing_metadata = [
        'absl-py',
        'pandas'
    ]

    for pkg in packages_needing_metadata:
        try:
            datas += copy_metadata(pkg, recursive=True)
        except Exception as e:
            print(f'Failed to copy metadata for {pkg}: {e}')

    return datas, binaries, hiddenimports

project_datas, project_binaries, project_hiddenimports = collect_dynamic_resources()

a = Analysis(
    ['evalbench/evalbench.py'],
    pathex=['.', 'evalbench'],
    binaries=project_binaries,
    datas=project_datas,
    hiddenimports=project_hiddenimports,
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
    name='evalbench',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
