# PyInstaller spec for Video Downloader.app. Run through packaging/build.sh.
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
ARCH = os.environ["VD_ARCH"]                     # arm64 or x86_64; must match this Python
VENDOR = ROOT / "packaging" / "vendor" / ARCH
VERSION = os.environ.get("VD_VERSION", "2.0.0")

datas = [
    (str(ROOT / "static"), "static"),
    (str(VENDOR / "licenses"), "licenses"),
    (str(ROOT / "LICENSE"), "licenses"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "licenses"),
]
binaries = [(str(VENDOR / "bin" / name), "bin") for name in ("yt-dlp", "ffmpeg", "ffprobe", "deno")]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["yt_dlp.cookies"],            # Arc cookie export
    excludes=["tkinter", "unittest", "pydoc", "PyQt5", "PyQt6", "PySide6", "gi"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Video Downloader",
    console=False,
    target_arch=ARCH,
    codesign_identity=None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Video Downloader", upx=False)
app = BUNDLE(
    coll,
    name="Video Downloader.app",
    icon=str(ROOT / "assets" / "icon" / "AppIcon.icns"),
    bundle_identifier="io.github.vathanaaaa.videodownloader",
    version=VERSION,
    info_plist={
        "CFBundleName": "Video Downloader",
        "CFBundleDisplayName": "Video Downloader",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "MIT License",
        # The Chrome extension's "Open app" button opens videodownloader://
        "CFBundleURLTypes": [{"CFBundleURLName": "Video Downloader", "CFBundleURLSchemes": ["videodownloader"]}],
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "NSLocalNetworkUsageDescription": "Lets your phone use Video Downloader when you turn on “Use from my phone”.",
    },
)
