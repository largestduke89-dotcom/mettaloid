# Mettaloid

A Medal-style game clipper for Windows, built as a **native Qt app with no Chromium anywhere**: no Electron, no WebView2, no QtWebEngine, and OBS's browser plugin (CEF) is removed. It records in the background, saves the last N seconds when you press a hotkey, and has an editor, a library, a size-targeted compressor and Find Frame.

| | Electron build (old) | Native build (now) |
|---|---|---|
| Mettaloid RAM, window open | ~530 MB | **~125 MB** |
| OBS recorder RAM | ~336 MB (CEF loaded) | **~200 MB** (no CEF) |
| Installer | 306 MB | **141 MB** |

## Architecture

- **UI**: Qt 6 Widgets via PySide6, with a dark theme. Pages are created lazily on first visit. The clip grid is virtualized: a painted delegate plus an async thumbnail cache capped in MB. Video plays through Qt's FFmpeg backend with GPU decoding. When Mettaloid sits in the tray, the whole UI is destroyed after 45 s and the working set is trimmed. Starting to the tray never builds the UI at all.
- **Recording engine**: OBS Studio **31.1.x**, unmodified apart from removed plugins. It runs hidden in portable mode and is driven over obs-websocket. `scripts/fetch_deps.py` strips the browser plugin (CEF/Chromium), WebRTC, VST, VLC, capture-card and scripting plugins. OBS 32+ treats every bundled plugin as required and its Chromium can't be removed, which is why 31.x is pinned (override with `OBS_VERSION`).
- **Media**: FFmpeg 9 (BtbN shared build) for the render pipeline: editor exports, compressor, montage, thumbnails and Find Frame decoding.
- **Find Frame engine**: OpenCV, based on [FindFrame](https://github.com/Gunbard/FindFrame) (MIT). It runs as `Mettaloid.exe --engine job.json` (no Qt loaded), so the bundle contains only one Python/OpenCV runtime.

```
mettaloid/
  __main__.py          entry (--hidden, --engine)
  app.py               QApplication, tray, overlay, single instance, free-UI-when-hidden
  core/                no UI code
    backend.py         owns services; all signals are delivered on the UI thread
    obs_manager.py     OBS lifecycle, replay/record, hot profile-swap reconfigure, stats, adaptive preset
    obs_config.py  obs_ws.py  planner.py  render.py  ffmpeg.py  library.py  games.py
    hotkeys.py (RegisterHotKey)  updater.py  findframe.py  settings.py  paths.py
  engine/findframe_engine.py
  ui/                  widgets, theme, icons, clip grid, video, overlay, main window
    pages/             home, library, viewer, editor (+timeline), compressor, findframe_page, games_page, settings_page
scripts/fetch_deps.py  downloads OBS 31 (stripped) + FFmpeg into vendor/
scripts/build.py       PyInstaller bundle + vendor + Inno Setup installer
installer/mettaloid.iss
legacy-electron/       the previous Electron implementation (reference only)
```

## Develop

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python scripts\fetch_deps.py
.venv\Scripts\python -m mettaloid
```

`MT_USER_DATA=<folder>` uses a separate profile. `MT_NO_RECORDER=1` runs the UI only, with no OBS, hotkeys or detection. `MT_SNAPSHOT_DIR` plus `MT_SNAPSHOT_ROUTES="home,library,clip:{first}"` visits each page and saves screenshots and tracebacks (see `ui/snapshot.py`).

## Build the installer

```bash
winget install JRSoftware.InnoSetup      # once
.venv\Scripts\python scripts\build.py   # → release\Mettaloid-Setup-<version>.exe
```

The build fails if any Chromium/WebEngine file ends up in the bundle.

## Auto-updates

1. Set `UPDATE_REPO = "your-github-name/mettaloid"` in `mettaloid/__init__.py`. The repo must be public.
2. Bump `VERSION` in that same file, commit, then `git tag v0.3.0 && git push --tags`.
3. `.github/workflows/release.yml` builds on a Windows runner and attaches `Mettaloid-Setup-x.y.z.exe` to the release.

Installed apps check every 4 hours (and 15 s after launch) and download in the background. On quit, the installer runs silently and relaunches Mettaloid. "Restart & update" is under Settings → Updates.

## Features

- **Recording**:
  - Instant replay of 5 s – 20 min, with several clip hotkeys of different lengths (F8 = last 60 s, Shift+F8 = last 15 s).
  - Full session recording with bookmarks.
  - Screenshots.
  - Auto-detection of 60+ games, plus custom games and per-game profiles.
- **Performance & memory**:
  - Replay RAM limit (automatic = 12% of RAM), with auto quality that picks the best resolution, fps and bitrate that fit.
  - Hot reconfiguration (~0.8 s, no OBS restart).
  - Encoder speed presets with adaptive downgrade if frames drop.
  - Recorder priority.
  - Gentle (low-priority) exports.
  - Live CPU/RAM/drop stats.
- **Audio**: 4 tracks (mix / game / mic / Discord), RNNoise, an option to record only the game's audio, and a mic hotkey.
- **Library**: virtualized grid, search, filters, albums, tags, favorites, bulk actions, drag & drop import, watch folders, and storage cleanup to the Recycle Bin.
- **Editor**:
  - Timeline with split, trim, per-segment speed, reorder and undo.
  - Crop, aspect ratios (blur/pad/fill), rotate and flip.
  - Looks and colour sliders, fades, draggable text.
  - Per-track audio preview, music, loudness normalization.
  - MP4/WebM/MKV/GIF export with size limits; montages.
- **Compressor**: 20 / 50 / 500 MB or custom size, two-pass x264 or GPU, verified under the target, smart downscale.
- **Find Frame**:
  - Pause a clip, press **B**, draw a box, then search this clip, the game or the whole library.
  - Saved finders, library scans, auto-scan of new clips, auto tags and bookmarks, and montages of matches.

## Licensing

OBS Studio (GPL-2.0+) and FFmpeg (GPL) are bundled and talked to as separate processes; PySide6/Qt is LGPL-3. The feature matching is based on FindFrame (MIT). Keep `assets/license.txt` and make the source available when distributing.
