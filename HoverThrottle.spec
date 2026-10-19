# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 설정: 단일 실행파일(--onefile --windowed) HoverThrottle.exe
# 빌드: pyinstaller --noconfirm --clean HoverThrottle.spec

from pathlib import Path

ROOT = Path(SPECPATH)

binaries = []
bbd = ROOT / "bin" / "blackbox_decode.exe"
if bbd.is_file():
    # 2순위 디코더 (GPL-3.0, README 라이선스 고지 참고)
    binaries.append((str(bbd), "bin"))
    print(f"[HoverThrottle] bundling {bbd}")
else:
    print("[HoverThrottle] bin/blackbox_decode.exe 없음 — orangebox 디코더만 포함합니다")

datas = [(str(ROOT / "assets" / "icon.ico"), "assets")]

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["matplotlib.backends.backend_qtagg", "orangebox", "orangebox.reader"],
    excludes=[
        "tkinter",
        "pytest",
        "IPython",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.QtPdf",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HoverThrottle",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "assets" / "icon.ico"),
)
