"""Download the runtime dependencies Mettaloid bundles:

  vendor/obs     - official OBS Studio build (recording engine), debug symbols stripped
  vendor/ffmpeg  - FFmpeg shared build (ffmpeg.exe + ffprobe.exe share one set of DLLs)

    python scripts/fetch_deps.py            # both
    python scripts/fetch_deps.py obs        # only OBS   (OBS_VERSION=33.0.1 to pin)
    python scripts/fetch_deps.py ffmpeg     # only FFmpeg
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor")
UA = {"User-Agent": "mettaloid-build", "Accept": "application/vnd.github+json"}
if os.environ.get("GITHUB_TOKEN"):
    UA["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]


def api(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA)) as r:
        return json.load(r)


def download(url, size=None):
    print("  downloading", url.rsplit("/", 1)[-1], f"({size / 1e6:.0f} MB)" if size else "")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})) as r:
        return r.read()


# OBS 32+ treats every bundled plugin as a required "core module", so its
# Chromium-based browser plugin (CEF) can't be removed. OBS 31 loads whatever
# plugins are present, so we pin the newest 31.x and strip CEF out entirely.
OBS_SERIES = os.environ.get("OBS_SERIES", "31.")
UNUSED_PLUGINS = ["obs-browser", "obs-webrtc", "aja", "aja-output-ui", "decklink", "decklink-captions", "decklink-output-ui",
                  "obs-vst", "vlc-video", "frontend-tools"]
CEF_FILES = re.compile(r"^(libcef\.dll|chrome_elf\.dll|obs-browser.*|cef.*\.pak|chrome_.*\.pak|resources\.pak|icudtl\.dat|snapshot_blob\.bin|"
                       r"v8_context_snapshot\.bin|vk_swiftshader.*|vulkan-1\.dll|libEGL\.dll|libGLESv2\.dll|d3dcompiler_47\.dll)$", re.I)


def prune_obs(dest):
    plugdir = os.path.join(dest, "obs-plugins", "64bit")
    removed = 0
    if os.path.isdir(plugdir):
        for f in os.listdir(plugdir):
            base = os.path.splitext(f)[0]
            if base in UNUSED_PLUGINS or CEF_FILES.match(f):
                p = os.path.join(plugdir, f)
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
                removed += 1
        shutil.rmtree(os.path.join(plugdir, "locales"), ignore_errors=True)
    for name in UNUSED_PLUGINS:
        shutil.rmtree(os.path.join(dest, "data", "obs-plugins", name), ignore_errors=True)
    for d, _, files in os.walk(dest):
        for f in files:
            if f.lower().endswith(".pdb"):
                os.remove(os.path.join(d, f))
    # scripting runtimes only used by the removed frontend-tools plugin
    for f in ("obs-scripting.dll", "obspython.py", "_obspython.pyd", "obslua.dll"):
        p = os.path.join(dest, "bin", "64bit", f)
        if os.path.exists(p):
            os.remove(p)
    leftover = [os.path.join(d, f) for d, _, fs in os.walk(dest) for f in fs if "cef" in f.lower() or "chrome" in f.lower()]
    if leftover:
        raise SystemExit("Chromium files still present in OBS: " + ", ".join(leftover[:5]))
    print(f"  removed {removed} browser/unused plugin files (no Chromium left in OBS)")


def fetch_obs():
    dest = os.path.join(VENDOR, "obs")
    tag = os.environ.get("OBS_VERSION")
    if tag:
        rel = api(f"https://api.github.com/repos/obsproject/obs-studio/releases/tags/{tag}")
    else:
        rels = [r for r in api("https://api.github.com/repos/obsproject/obs-studio/releases?per_page=100")
                if r["tag_name"].startswith(OBS_SERIES) and not r["prerelease"] and not r["draft"]]
        rel = max(rels, key=lambda r: tuple(int(x) for x in re.findall(r"\d+", r["tag_name"])[:3]))
    asset = next(a for a in rel["assets"] if re.search(r"windows(-x64)?\.zip$", a["name"], re.I) and not re.search(r"pdb|arm64|beta|rc\d", a["name"], re.I))
    stamp = os.path.join(dest, ".mt-obs-version")
    if os.path.exists(stamp) and open(stamp).read() == rel["tag_name"]:
        print(f"OBS {rel['tag_name']} already present")
        return
    data = download(asset["browser_download_url"], asset["size"])
    tmp = tempfile.mkdtemp(dir=VENDOR if os.path.isdir(VENDOR) else None)
    zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
    root = None
    for d, _, files in os.walk(tmp):
        if "obs64.exe" in files and d.replace("\\", "/").endswith("bin/64bit"):
            root = os.path.dirname(os.path.dirname(d))
            break
    if not root:
        raise SystemExit("obs64.exe not found in archive")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.move(root, dest)
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(os.path.join(dest, "config"), ignore_errors=True)
    if os.path.isdir(os.path.join(dest, "core")):
        print("  warning: OBS 32+ layout - its Chromium browser plugin cannot be removed")
    else:
        prune_obs(dest)
    open(stamp, "w").write(rel["tag_name"])
    print(f"OBS {rel['tag_name']} ready in vendor/obs")


def fetch_ffmpeg():
    dest = os.path.join(VENDOR, "ffmpeg")
    rel = api("https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest")
    best = None
    for a in rel["assets"]:
        m = re.match(r"ffmpeg-n(\d+)\.(\d+)-latest-win64-gpl-shared-[\d.]+\.zip$", a["name"])
        if m and (best is None or (int(m[1]), int(m[2])) > best[0]):
            best = ((int(m[1]), int(m[2])), a)
    if not best:
        raise SystemExit("no FFmpeg shared build found")
    ver, asset = best
    stamp = os.path.join(dest, ".mt-ffmpeg-version")
    if os.path.exists(stamp) and open(stamp).read() == asset["name"]:
        print(f"FFmpeg {ver[0]}.{ver[1]} already present")
        return
    data = download(asset["browser_download_url"], asset["size"])
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    z = zipfile.ZipFile(io.BytesIO(data))
    for n in z.namelist():
        base = n.rsplit("/", 1)[-1]
        if "/bin/" in n and base and (base.endswith(".dll") or base in ("ffmpeg.exe", "ffprobe.exe")):
            with z.open(n) as src, open(os.path.join(dest, base), "wb") as out:
                shutil.copyfileobj(src, out)
    lic = next((n for n in z.namelist() if n.endswith("LICENSE.txt")), None)
    if lic:
        with z.open(lic) as src, open(os.path.join(dest, "LICENSE.txt"), "wb") as out:
            shutil.copyfileobj(src, out)
    open(stamp, "w").write(asset["name"])
    print(f"FFmpeg {ver[0]}.{ver[1]} ready in vendor/ffmpeg")


if __name__ == "__main__":
    os.makedirs(VENDOR, exist_ok=True)
    what = sys.argv[1:] or ["ffmpeg", "obs"]
    if "ffmpeg" in what:
        fetch_ffmpeg()
    if "obs" in what:
        fetch_obs()
