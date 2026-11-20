"""Build Mettaloid: PyInstaller bundle + vendored OBS/FFmpeg + Inno Setup installer.

    python scripts/build.py            → dist/Mettaloid/ and release/Mettaloid-Setup-<version>.exe
    python scripts/build.py --no-installer
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mettaloid import VERSION  # noqa: E402

DIST = os.path.join(ROOT, "dist", "Mettaloid")

# Qt modules Mettaloid never uses. Nothing Chromium-based (QtWebEngine*) may ship.
EXCLUDE = [
    "tkinter", "unittest", "pydoc", "matplotlib", "IPython",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtWebView", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtQml", "PySide6.QtQuickControls2",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtHttpServer", "PySide6.QtXml",
]


def run(cmd, **kw):
    print(">", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT, **kw)


def smoke_test():
    """Launch the packaged app (UI only, throwaway profile) and make sure it starts
    and renders every page. Catches missing modules before anything is published."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="mt-smoke-")
    ud, shots = os.path.join(tmp, "ud"), os.path.join(tmp, "shots")
    os.makedirs(ud)
    with open(os.path.join(ud, "settings.json"), "w") as f:
        f.write('{"general": {"clipFolder": "%s", "launchOnStartup": false}}' % os.path.join(tmp, "clips").replace("\\", "/"))
    env = dict(os.environ, MT_NO_RECORDER="1", MT_USER_DATA=ud, MT_SNAPSHOT_DIR=shots,
               MT_SNAPSHOT_ROUTES="home,library,compressor,findframe,games,settings", MT_SNAPSHOT_WAIT="1500")
    try:
        subprocess.run([os.path.join(DIST, "Mettaloid.exe")], env=env, timeout=180)
    except subprocess.TimeoutExpired:
        raise SystemExit("smoke test: app did not exit")
    log = open(os.path.join(ud, "logs", "mettaloid.log"), encoding="utf-8").read() if os.path.exists(os.path.join(ud, "logs", "mettaloid.log")) else ""
    errs = open(os.path.join(shots, "errors.txt"), encoding="utf-8").read().strip() if os.path.exists(os.path.join(shots, "errors.txt")) else "no errors.txt (app crashed?)"
    pages = [f for f in os.listdir(shots)] if os.path.isdir(shots) else []
    if errs or "CRITICAL" in log or len([p for p in pages if p.endswith(".png")]) < 6:
        print(log[-3000:])
        raise SystemExit(f"smoke test FAILED: {errs[:2000]}")
    # the Find Frame engine entry point must work too
    out = subprocess.run([os.path.join(DIST, "Mettaloid.exe"), "--engine", "--version"], capture_output=True, text=True, timeout=60).stdout
    if '"version"' not in out:
        raise SystemExit("smoke test FAILED: engine entry point: " + out[:500])
    print("smoke test passed: app starts, all pages render, engine runs")


def main():
    if not os.path.exists(os.path.join(ROOT, "vendor", "obs", "bin", "64bit", "obs64.exe")) or not os.path.exists(os.path.join(ROOT, "vendor", "ffmpeg", "ffmpeg.exe")):
        run([sys.executable, "scripts/fetch_deps.py"])
    entry = os.path.join(ROOT, "build", "run_mettaloid.py")
    os.makedirs(os.path.dirname(entry), exist_ok=True)
    with open(entry, "w") as f:
        f.write("import sys\nfrom mettaloid.__main__ import main\nsys.exit(main())\n")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "Mettaloid",
            "--icon", os.path.join(ROOT, "assets", "icon.ico"), "--distpath", os.path.join(ROOT, "dist"), "--workpath", os.path.join(ROOT, "build", "pyi"),
            "--specpath", os.path.join(ROOT, "build"), "--paths", ROOT,
            "--add-data", f"{os.path.join(ROOT, 'mettaloid', 'data')};mettaloid/data",
            "--add-data", f"{os.path.join(ROOT, 'assets')};assets",
            "--hidden-import", "mettaloid.engine.findframe_engine", "--collect-submodules", "mettaloid"]
    for m in EXCLUDE:
        args += ["--exclude-module", m]
    run(args + [entry])

    internal = os.path.join(DIST, "_internal")
    removed = 0
    for d, _, files in os.walk(internal):
        for f in files:
            low = f.lower()
            # OpenCV's private FFmpeg (we decode through vendor/ffmpeg), WebEngine leftovers, debug symbols
            if (re.match(r"opencv_videoio_ffmpeg.*\.dll$", low) or "webengine" in low or low.endswith(".pdb")
                    or re.match(r"qt6(quick|qml|pdf|virtualkeyboard)\w*\.dll$", low) or low == "opengl32sw.dll"
                    or re.match(r"lib(crypto|ssl)-3-x64\.dll$", low)):
                os.remove(os.path.join(d, f))
                removed += 1
    # Qt plugins Mettaloid doesn't use (virtual keyboard, TLS backends, network info)
    plug = os.path.join(internal, "PySide6", "plugins")
    for sub in ("platforminputcontexts", "tls", "networkinformation", "generic"):
        shutil.rmtree(os.path.join(plug, sub), ignore_errors=True)
    shutil.rmtree(os.path.join(internal, "PySide6", "translations"), ignore_errors=True)
    print(f"pruned {removed} unused files")
    bad = [os.path.join(d, f) for d, _, fs in os.walk(DIST) for f in fs if "webengine" in f.lower() or "chrom" in f.lower()]
    if bad:
        raise SystemExit("Chromium components found in bundle: " + ", ".join(bad[:5]))

    vend = os.path.join(DIST, "vendor")
    shutil.rmtree(vend, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "vendor", "ffmpeg"), os.path.join(vend, "ffmpeg"))
    shutil.copytree(os.path.join(ROOT, "vendor", "obs"), os.path.join(vend, "obs"), ignore=shutil.ignore_patterns("config", "*.pdb"))
    print("bundle ready:", DIST)
    smoke_test()

    if "--no-installer" in sys.argv:
        return
    iscc = next((p for p in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
                             r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", shutil.which("iscc") or "") if p and os.path.exists(p)), None)
    if not iscc:
        raise SystemExit("Inno Setup (ISCC.exe) not found — install it: winget install JRSoftware.InnoSetup")
    run([iscc, f"/DAppVersion={VERSION}", os.path.join(ROOT, "installer", "mettaloid.iss")])
    print("installer:", os.path.join(ROOT, "release", f"Mettaloid-Setup-{VERSION}.exe"))


if __name__ == "__main__":
    main()
