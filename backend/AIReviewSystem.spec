# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

# `app/data` 是**大类赛道映射表**（V1.009.5）：theme_taxonomy.json 由
# backend/build_theme_taxonomy.py 离线生成。打包后 exe 没有 MCP 环境，
# 拉不到申万/产业链骨架，所以必须把这份静态映射一起打进去 ——
# 漏了它的表现是赛道列全部显示 `-`（theme_taxonomy 加载失败会降级为 None，不报错）。
datas = [('../frontend/dist', 'dist'), ('.env', '.'), ('app/data', 'app/data')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('uvicorn')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('fastapi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('starlette')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('openpyxl')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['launcher.py'],
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
    [],
    exclude_binaries=True,
    name='AIReviewSystem',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AIReviewSystem',
)
