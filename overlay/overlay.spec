# -*- mode: python ; coding: utf-8 -*-

datas = [
    ('cards.json', '.'),
]

hiddenimports = [
    # 로컬 모듈 (함수 내 동적 import 포함)
    'engine',
    'matcher',
    'capture',
    'claude_ocr',
    'gemini_client',
    'card_parser',
    'state_parser',
    'combat_recommender',
    # winrt OCR
    'winrt.windows.media.ocr',
    'winrt.windows.graphics.imaging',
    'winrt.windows.storage.streams',
    'winrt.windows.globalization',
    'winrt.windows.foundation',
    # Gemini
    'google.genai',
    'google.genai.types',
    'google.auth',
    'google.auth.transport.requests',
    # 기타
    'PIL',
    'PIL.Image',
    'PIL.ImageEnhance',
    'PIL.ImageFilter',
    'mss',
    'rapidfuzz',
    'rapidfuzz.fuzz',
    'rapidfuzz.process',
    'anthropic',
    'numpy',
]

a = Analysis(
    ['overlay.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'easyocr',
        'torch', 'torchvision', 'torchaudio',
        'scipy', 'sklearn', 'matplotlib',
        'pandas', 'notebook',
        'IPython', 'jupyter', 'tensorflow',
        'cv2', 'tkinter', 'wx',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='STS2Overlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='STS2Overlay',
)
