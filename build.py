"""
Budowanie samodzielnej wersji LaserBurn Studio dla Windows.

    .venv\\Scripts\\python.exe -m pip install -r requirements-build.txt
    .venv\\Scripts\\python.exe build.py            # program + instalator
    .venv\\Scripts\\python.exe build.py --no-installer

Kroki:
 1. PyInstaller (tryb katalogowy, bez konsoli) → dist\\LaserBurnStudio\\LaserBurnStudio.exe
 2. Test uruchomienia spakowanego programu (--smoke-test)
 3. Archiwum przenośne  → dist\\LaserBurnStudio-<wersja>-portable.zip
 4. Inno Setup (ISCC)   → dist\\LaserBurnStudio-<wersja>-setup.exe  (gdy Inno Setup 6 jest zainstalowany)

Wynik nie wymaga Pythona ani bibliotek na komputerze docelowym (Windows 10/11, 64-bit).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from app_info import APP_NAME, APP_VERSION, APP_PUBLISHER  # noqa: E402

EXE_NAME = "LaserBurnStudio"
DIST = ROOT / "dist"
BUILD = ROOT / "build"

# Moduły, których program nie używa, a które PyInstaller mógłby dociągnąć (rozmiar instalatora)
EXCLUDES = [
    "tkinter", "unittest", "pydoc_data", "matplotlib", "scipy", "pandas", "IPython", "pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
]


def _version_tuple() -> tuple:
    parts = [int(p) for p in APP_VERSION.split(".") if p.isdigit()]
    return tuple((parts + [0, 0, 0, 0])[:4])


def write_version_file(path: Path):
    """Metadane pliku .exe (Właściwości → Szczegóły w Eksploratorze)."""
    v = _version_tuple()
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041504B0', [
      StringStruct('CompanyName', '{APP_PUBLISHER}'),
      StringStruct('FileDescription', '{APP_NAME} – CAD/CAM dla laserów GRBL'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('InternalName', '{EXE_NAME}'),
      StringStruct('OriginalFilename', '{EXE_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{APP_VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [0x0415, 1200])])
  ]
)
""", encoding="utf-8")


def run_pyinstaller():
    BUILD.mkdir(exist_ok=True)
    version_file = BUILD / "version_info.txt"
    write_version_file(version_file)
    sep = os.pathsep
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", EXE_NAME,
        "--icon", str(ROOT / "assets" / "laserburn.ico"),
        "--version-file", str(version_file),
        "--add-data", f"{ROOT / 'assets'}{sep}assets",
        "--distpath", str(DIST), "--workpath", str(BUILD), "--specpath", str(BUILD),
    ]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    args.append(str(ROOT / "laserburn_studio.py"))
    print(">>", " ".join(args[:6]), "...")
    subprocess.run(args, check=True, cwd=ROOT)


# Pliki Qt dociągane przez wtyczki, których program nie używa (klawiatura ekranowa → Quick/QML/OpenGL,
# podgląd PDF, programowy OpenGL). Usunięcie ~45 MB; test uruchomienia po przycięciu to weryfikuje.
PRUNE = [
    "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
    "PySide6/plugins/imageformats/qpdf.dll",
    "PySide6/Qt6VirtualKeyboard.dll", "PySide6/Qt6Quick.dll", "PySide6/Qt6Qml.dll", "PySide6/Qt6QmlMeta.dll",
    "PySide6/Qt6QmlModels.dll", "PySide6/Qt6QmlWorkerScript.dll", "PySide6/Qt6OpenGL.dll", "PySide6/Qt6Pdf.dll",
    "PySide6/opengl32sw.dll",
]
KEEP_TRANSLATIONS = ("qtbase_pl", "qtbase_en", "qt_pl", "qt_en")


def prune(app_dir: Path):
    internal = app_dir / "_internal"
    freed = 0
    for rel in PRUNE:
        f = internal / rel
        if f.is_file():
            freed += f.stat().st_size
            f.unlink()
    tr = internal / "PySide6" / "translations"
    if tr.is_dir():
        for f in tr.glob("*.qm"):
            if not f.stem.startswith(KEEP_TRANSLATIONS):
                freed += f.stat().st_size
                f.unlink()
    print(f">> przycięto zbędne pliki Qt: {freed / 2 ** 20:.0f} MB")


def copy_docs(app_dir: Path):
    """Instrukcja i rysunki obok programu (otwierane z menu Start)."""
    doc_dir = app_dir / "docs"
    doc_dir.mkdir(exist_ok=True)
    for name in ("README.md", "instrukcja.md"):
        shutil.copy2(ROOT / name, app_dir / name)
    for img in (ROOT / "docs").glob("*.png"):
        shutil.copy2(img, doc_dir / img.name)


def smoke_test(exe: Path):
    print(">> test uruchomienia:", exe.name, "--smoke-test")
    log = BUILD / "smoke.log"
    log.unlink(missing_ok=True)
    env = dict(os.environ, LASERBURN_MATERIALS=str(BUILD / "smoke_materials.json"), LASERBURN_SMOKE_LOG=str(log))
    r = subprocess.run([str(exe), "--smoke-test"], timeout=180, env=env)
    report = log.read_text(encoding="utf-8") if log.exists() else "(brak raportu – sprawdź ~/.laserburn_studio/laserburn.log)"
    if r.returncode != 0:
        raise SystemExit(f"Spakowany program zakończył się kodem {r.returncode}:\n{report}")
    print("  ", report)


def make_zip(app_dir: Path) -> Path:
    out = DIST / f"{EXE_NAME}-{APP_VERSION}-portable.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in app_dir.rglob("*"):
            if f.is_file():
                z.write(f, Path(EXE_NAME) / f.relative_to(app_dir))
    return out


def find_iscc() -> Path | None:
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ]
    return next((c for c in candidates if c.is_file()), None)


def make_installer(app_dir: Path) -> Path | None:
    iscc = find_iscc()
    if iscc is None:
        print("!! Nie znaleziono Inno Setup 6 (ISCC.exe) – pomijam instalator.")
        print("   Instalacja: winget install JRSoftware.InnoSetup   (lub https://jrsoftware.org/isdl.php)")
        return None
    subprocess.run([
        str(iscc), "/Qp",
        f"/DAppVersion={APP_VERSION}", f"/DAppName={APP_NAME}", f"/DAppPublisher={APP_PUBLISHER}",
        f"/DSourceDir={app_dir}", f"/DOutputDir={DIST}", f"/DExeName={EXE_NAME}",
        str(ROOT / "installer" / "laserburn_studio.iss"),
    ], check=True, cwd=ROOT)
    return DIST / f"{EXE_NAME}-{APP_VERSION}-setup.exe"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-installer", action="store_true", help="bez Inno Setup")
    ap.add_argument("--no-test", action="store_true", help="bez testu uruchomienia")
    a = ap.parse_args()
    if sys.platform != "win32":
        print("Uwaga: skrypt przygotowano dla Windows; na innych systemach powstanie tylko katalog programu.")

    run_pyinstaller()
    app_dir = DIST / EXE_NAME
    exe = app_dir / (EXE_NAME + (".exe" if sys.platform == "win32" else ""))
    prune(app_dir)
    copy_docs(app_dir)
    if not a.no_test:
        smoke_test(exe)
    size = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file()) / 2 ** 20
    print(f">> program: {app_dir}  ({size:.0f} MB)")
    z = make_zip(app_dir)
    print(f">> przenośny: {z}  ({z.stat().st_size / 2 ** 20:.0f} MB)")
    if not a.no_installer and sys.platform == "win32":
        setup = make_installer(app_dir)
        if setup and setup.exists():
            print(f">> instalator: {setup}  ({setup.stat().st_size / 2 ** 20:.0f} MB)")


if __name__ == "__main__":
    main()
