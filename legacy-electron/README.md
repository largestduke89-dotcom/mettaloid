# Mettaloid

A Medal-style game clipper for Windows. It records gameplay in the background, saves the last N seconds when you press a hotkey, and includes an editor, a library, and a compressor that hits an exact file size.

The recording engine is a bundled copy of **OBS Studio** (unmodified). OBS runs hidden in portable mode, and Mettaloid controls it through the built-in obs-websocket. OBS handles capture, hardware encoding (NVENC / AMF / QuickSync / x264), the replay buffer and session recording. Mettaloid handles everything else.

## Features

- **Instant replay** with an adjustable buffer length (5 s – 20 min) from a slider on Home or in Settings. You can set several clip hotkeys, each with its own length (e.g. `F8` = last 60 s, `Shift+F8` = last 15 s). The buffer automatically covers your longest hotkey.
- **Automatic game detection** for 60+ games, plus custom games added by picking their `.exe`. Each game can have its own recording profile (resolution, FPS, replay length, mic). Games can also be ignored.
- **Full session recording** that can start and stop automatically with the game. Press the bookmark hotkey during a session to mark moments, then use "Clip it" in the viewer to cut each one.
- **Multi-track audio**: Track 1 is the mix, 2 is game/desktop, 3 is the microphone and 4 is Discord. You can capture only the game's audio, use RNNoise mic noise suppression, and toggle the mic with a hotkey.
- **Screenshots**, an in-game notification overlay, capture sounds, and a tray icon. Mettaloid can launch with Windows.
- **Auto-clip API**: a local HTTP endpoint (`POST /clip`, `POST /bookmark`) that scripts, mods or a Stream Deck can call to clip game events.
- **Library**: grid view grouped by day, hover previews, search, filters (type, game, favorites), albums, tags, multi-select bulk actions, drag & drop import, watch folders (for OBS/ShadowPlay output), storage stats and opt-in auto-cleanup (deleted clips go to the Recycle Bin; favorites and album clips are protected).
- **Editor** (non-destructive, auto-saves drafts):
  - Timeline with split, trim handles, delete, duplicate, reorder, per-segment speed (0.25x–4x), and session bookmarks shown as markers
  - Crop (draggable box plus presets), aspect ratios (16:9, 9:16, 1:1, 4:5, 21:9) with fill / blurred bars / black bars, rotate, and flip
  - Color looks plus brightness, contrast, saturation, gamma, hue, sharpen and vignette; fade in and fade out
  - Text overlays you can drag in the preview, with timing, size, color, outline and box
  - Per-track audio volume and mute with real per-track preview, background music, and loudness normalization
  - Export to MP4, WebM, MKV or GIF at any resolution and FPS, with H.264 or HEVC, GPU or CPU encoding, an optional size limit, and a lossless fast-trim mode
  - Montage: select several clips in the Library and choose "Montage"
- **Compressor**: target **20 MB, 50 MB, 500 MB**, or a custom size. It uses two-pass x264 (or GPU encoders) and checks the output size after encoding, retrying if it lands over the target. Smart downscaling lowers resolution or FPS when the bitrate would be too low. It shows an estimate before encoding, supports trimming, lets you choose which audio to keep, and has one-click "Copy file" for pasting into Discord.
- **Installer and auto-updates** from GitHub Releases.

## Find Frame

Find every moment an object appears across your clips. Pause any clip, press **Find Frame** (or `B`), and draw a box around something (a kill icon, a victory banner, an item, a map spot):

- **Search** this clip, every clip from the same game, or your whole library. Matches stream in live with thumbnails, timestamps and confidence. **Clip it** opens the editor around a match, and **Montage of matches** builds an edit from all of them.
- **Save finder** keeps the reference in the *Saved finders* tab. Saved finders can scan the whole library (*Scan library* tab: filter by game, type, date and favorites, with incremental re-scans), and can automatically check every new clip as it's saved. Matching clips can be auto-tagged and bookmarked; bookmarks show on the editor timeline.
- **Matching methods**: *Features* is the ORB keypoint matcher from [FindFrame](https://github.com/Gunbard/FindFrame) by Gunbard (MIT), extended with RANSAC geometry verification. *Exact look* is multi-scale template matching with a coarse-to-fine search, best for fixed HUD elements. *Auto* picks based on how much detail the box has.
- **Settings**: thresholds, keypoint count, ratio test, matcher, search area (anywhere or same spot), scales, contrast/CLAHE/edge/blur preprocessing, analysis fps and resolution, GPU decode, parallel scans, threads, low priority / pause while gaming, merge gap, per-clip limits, and automation.

The engine (`findframe-engine/engine.py`) is a standalone Python/OpenCV program that takes a JSON job and prints JSON lines. It decodes frames through Mettaloid's FFmpeg and is UI-independent, so a future native frontend can reuse it as-is. Build it with `npm run build:engine` (needs Python 3.12+); this produces `findframe-engine/dist/findframe-engine/findframe-engine.exe`, which the installer bundles.

## Performance & memory

- **Replay memory limit**: the RAM the replay buffer is allowed to use. "Automatic" uses about 12% of system RAM (512 MB – 6 GB). It is also passed to OBS as a hard cap (`RecRBSize`).
- **Auto quality**: works out the best resolution, frame rate and bitrate that keep your whole replay length inside that limit. Inputs are the limit, the longest clip hotkey, the codec, the number of audio tracks, and your max-resolution / max-FPS ceilings. It uses CBR so memory use is predictable, lowers audio bitrate when memory is tight, and stops adding bitrate once more wouldn't visibly help. The result (e.g. "1440p60 · 44 Mbps · Excellent · 321 MB") is shown live in Settings and on Home. Per-game profiles act as ceilings.
- **Hot reconfiguration**: settings changes are written to an inactive OBS profile, and OBS switches to it in about 1 second without restarting.
- **Encoder speed presets** (Quality / Balanced / Performance / Max perf) for NVENC, AMF, QSV and x264. **Adaptive performance** switches to a faster preset automatically if the encoder drops more than 2% of frames during a game. It warns you if the GPU itself is overloaded.
- **Recorder process priority** (Above normal by default). Live recorder CPU, RAM, render time and drop rates are shown on Home.
- **Low app overhead**:
  - The UI isn't created when starting to the tray, and is unloaded about 45 s after being hidden.
  - The overlay window is freed when idle.
  - While a game runs, detection only checks that game's process ID (no extra processes spawned); full scans run every 10 s.
  - Thumbnail work is limited to 2 at a time at low priority.
  - Exports run at below-normal priority with capped threads while gaming.

## Development

```bash
npm install
npm run fetch-obs     # downloads OBS Studio into vendor/obs (~470 MB unpacked)
npm run dev           # Vite + Electron with hot reload
```

If `vendor/obs` is missing, development builds fall back to an installed OBS Studio (`C:\Program Files\obs-studio`) and copy it into app data, because portable mode needs a writable folder. You can also choose a custom OBS folder under Settings → Advanced.

npm 11+ blocks install scripts by default. `package.json` already approves `electron`, `ffmpeg-static` and `esbuild` via `allowScripts`.

## Building the installer

```bash
npm run fetch-obs
npm run build:engine  # Find Frame engine (Python 3.12+ required)
npm run dist          # → release/Mettaloid-Setup-<version>.exe
```

> **Windows note:** if electron-builder fails with *"Cannot create symbolic link: A required privilege is not held"*, either enable Windows Developer Mode, or extract `winCodeSign-2.6.0.7z` into `%LOCALAPPDATA%\electron-builder\Cache\winCodeSign\winCodeSign-2.6.0` without its `darwin` folder. The GitHub Actions runner doesn't have this problem.

## Auto-updates / GitHub setup

1. Create a GitHub repository (e.g. `mettaloid`) and push this project to it.
2. In `package.json`, set `build.publish[0].owner` to your GitHub username, and change `repo` if you used a different name.
3. Release by bumping `version` in `package.json`, then:
   ```bash
   git commit -am "v0.2.0"
   git tag v0.2.0
   git push && git push --tags
   ```
   `.github/workflows/release.yml` builds the installer on a Windows runner and publishes a GitHub Release with `Mettaloid-Setup-x.y.z.exe`, `latest.yml` and the blockmap.
4. Installed copies check for updates every 4 hours (and 15 s after launch). They download in the background (differential downloads via the blockmap) and install on the next restart, or immediately via Settings → Updates → "Restart & update".

Optional: set a repository variable `OBS_VERSION` (e.g. `33.0.1`) to pin the bundled OBS version. Otherwise the latest OBS release is used.

The repository must be **public** for updates to work without a token (electron-updater reads public releases).

## Auto-clip API

```bash
curl -X POST http://127.0.0.1:37737/clip -d '{"event":"Ace","before":20,"after":5}'
curl -X POST http://127.0.0.1:37737/bookmark -d '{"label":"Clutch"}'
curl http://127.0.0.1:37737/status
```

The API listens on localhost only and rejects browser (cross-origin) requests. While a session is recording, events become bookmarks.

## Project layout

```
electron/
  main.js            app lifecycle, IPC, tray, jobs, media:// streaming protocol
  obs/manager.js     launches OBS, websocket control, replay/record/game switching
  obs/config-writer  generates OBS profile, encoder and websocket config
  media/render.js    FFmpeg pipeline for the editor, compressor and montage
  media/ffmpeg.js    probe, thumbnails, lossless trims, HW encoder detection
  library.js         clip DB, albums, imports, watch folders, cleanup
  games/             process-based game detection + game list
  hotkeys.js overlay.js updater.js api-server.js settings.js
src/                 React UI (Home, Library, Viewer, Editor, Compressor, Games, Settings)
scripts/fetch-obs.mjs
```

## Licensing

OBS Studio is GPL-2.0-or-later and the bundled static FFmpeg is GPL-3.0. Mettaloid ships them unmodified and talks to OBS over a socket. If you distribute Mettaloid, keep the license notices (`build/license.txt`) and make the source available. Mettaloid itself is licensed GPL-2.0-or-later to keep this simple.
