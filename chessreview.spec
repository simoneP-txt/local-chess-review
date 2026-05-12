# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Chess.com Local Review.
# Build with:  pyinstaller --noconfirm chessreview.spec
# (or run .\build.ps1 from the project root)

from pathlib import Path

block_cipher = None

# Files/folders to bundle alongside the .exe. Tuple format is (source, target_dir_in_bundle).
datas = [
    ('templates', 'templates'),
    ('static', 'static'),
    ('engine/README.txt', 'engine'),
]

# Stockfish is only bundled if present at build time; otherwise the user
# can drop their own engine/stockfish.exe next to the generated ChessReview.exe.
if Path('engine/stockfish.exe').exists():
    datas.append(('engine/stockfish.exe', 'engine'))


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ChessReview',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX can make Windows Defender more aggressive; keep off by default
    console=True,         # keep True so users can see startup logs and errors
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ChessReview',
)
