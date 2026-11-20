// Mettaloid main process: wires OBS recorder, library, editor/compressor jobs,
// hotkeys, tray, overlay, auto-clip API and auto-updates together.
const { app, BrowserWindow, Tray, Menu, ipcMain, dialog, shell, screen, protocol, nativeImage, Notification } = require('electron');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { Readable } = require('stream');
const { execFile } = require('child_process');

protocol.registerSchemesAsPrivileged([
  { scheme: 'mtmedia', privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true, bypassCSP: true, corsEnabled: true } }
]);

if (process.env.MT_USER_DATA && !app.isPackaged) app.setPath('userData', process.env.MT_USER_DATA);

if (!app.requestSingleInstanceLock()) {
  app.quit();
  process.exit(0);
}
app.setAppUserModelId('com.mettaloid.app');

const { Settings } = require('./settings');
const { ObsManager } = require('./obs/manager');
const { Library, sanitize, uniquePath } = require('./library');
const { GameDetector, builtinGames } = require('./games/detector');
const { Hotkeys } = require('./hotkeys');
const { Overlay } = require('./overlay');
const { Updater } = require('./updater');
const { startApiServer } = require('./api-server');
const { FindFrame } = require('./findframe');
const { render, estimate } = require('./media/render');
const { probe, run, hwEncoders } = require('./media/ffmpeg');
const os = require('os');
const { planQuality, autoMemoryMB } = require('./quality-planner');

// Lean Chromium: no spare renderer, no background networking/spellcheck.
app.commandLine.appendSwitch('disable-features', 'SpareRendererForSitePerProcess,MediaRouter,Translate,SpellCheck' + (process.env.MT_SNAPSHOT_DIR ? ',CalculateNativeWinOcclusion' : ''));
app.commandLine.appendSwitch('disable-background-networking');

let settings, obs, library, detector, hotkeys, overlay, updater, api, findFrame;
let mainWindow = null;
let freeUiTimer = null;
let tray = null;
let quitting = false;
const jobs = new Map();

const ICON = path.join(__dirname, '..', 'build', 'icon.png');
const startHidden = process.argv.includes('--hidden');

function send(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
}

function notify(title, subtitle, kind = '', sound = 'clip') {
  overlay.toast({ title, subtitle, kind, sound });
  send('toast', { title, subtitle, kind });
}

// ---------------- windows ----------------
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 880,
    minWidth: 1040,
    minHeight: 640,
    show: false,
    backgroundColor: '#0d0e12',
    title: 'Mettaloid',
    icon: fs.existsSync(ICON) ? ICON : undefined,
    frame: false,
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#0d0e12', symbolColor: '#c9ccd6', height: 38 },
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      sandbox: false,
      nodeIntegration: false
    }
  });
  if (process.env.VITE_DEV_SERVER_URL) mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  else mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  mainWindow.once('ready-to-show', () => {
    if (!createWindow.deferShow) mainWindow.show();
    createWindow.deferShow = false;
  });
  mainWindow.on('close', (e) => {
    if (!quitting && settings.get().general.closeToTray) {
      e.preventDefault();
      mainWindow.hide();
    }
  });
  // Unload the whole UI renderer (~150-250 MB) after a while in the tray.
  mainWindow.on('hide', () => {
    clearTimeout(freeUiTimer);
    if (!settings.get().performance.freeUiWhenHidden) return;
    freeUiTimer = setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible() && ![...jobs.values()].some((j) => j.status === 'running' && j.watched)) {
        mainWindow.destroy();
        mainWindow = null;
      }
    }, 45000);
  });
  mainWindow.on('show', () => clearTimeout(freeUiTimer));
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
}

function showWindow(route) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
    if (route) mainWindow.webContents.once('did-finish-load', () => send('navigate', route));
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
  if (route) send('navigate', route);
}

function trayIcon() {
  if (fs.existsSync(ICON)) return nativeImage.createFromPath(ICON).resize({ width: 16, height: 16 });
  return nativeImage.createEmpty();
}

function updateTray() {
  if (!tray) return;
  const st = obs.status;
  const label = st.game ? `${st.game.name} — ${st.replayActive ? 'Replay on' : 'Replay off'}` : st.replayActive ? 'Replay buffer on' : 'Waiting for game';
  tray.setToolTip(`Mettaloid\n${label}${st.recording ? '\n● Recording' : ''}`);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label, enabled: false },
    { type: 'separator' },
    { label: 'Open Mettaloid', click: () => showWindow() },
    { label: `Save clip (${settings.get().hotkeys.clips[0]?.accelerator || ''})`, enabled: st.replayActive, click: () => actions.clip() },
    { label: st.recording ? 'Stop recording' : 'Start recording', enabled: st.obsState === 'ready', click: () => actions.toggleRecording() },
    { label: st.replayActive ? 'Pause replay buffer' : 'Start replay buffer', enabled: st.obsState === 'ready', click: () => actions.toggleReplay() },
    { label: st.micMuted ? 'Unmute microphone' : 'Mute microphone', click: () => actions.toggleMic() },
    { type: 'separator' },
    { label: 'Open clip folder', click: () => shell.openPath(settings.get().general.clipFolder) },
    { label: 'Settings', click: () => showWindow('/settings') },
    { type: 'separator' },
    { label: 'Quit Mettaloid', click: () => { quitting = true; app.quit(); } }
  ]));
}

// ---------------- actions ----------------
const actions = {
  async clip(seconds, label) {
    const s = settings.get();
    const secs = seconds || s.recording.replaySeconds;
    try {
      const raw = await obs.saveClip({ seconds: secs, label, source: 'hotkey' });
      notify('Clip saved', `${obs.status.game ? obs.status.game.name + ' · ' : ''}last ${formatSecs(secs)}`);
      return { ok: true, raw };
    } catch (e) {
      notify('Could not save clip', e.message, 'err', 'error');
      return { ok: false, error: e.message };
    }
  },
  async toggleRecording() {
    try {
      const wasRecording = obs.status.recording;
      await obs.toggleRecording();
      notify(wasRecording ? 'Recording saved' : 'Recording started', wasRecording ? 'Processing session…' : 'Press again to stop', 'rec', 'rec');
    } catch (e) {
      notify('Recording failed', e.message, 'err', 'error');
    }
  },
  async bookmark(label = 'Bookmark') {
    if (obs.status.recording) {
      const bm = obs.addBookmark(label);
      notify('Bookmark added', `at ${formatSecs(bm.time)}`, 'mark', 'mark');
      return bm;
    }
    // Not recording a session: a bookmark becomes a short clip instead.
    return actions.clip(30, label);
  },
  async screenshot() {
    try {
      const dir = path.join(library.gameDir(obs.status.game), 'Screenshots');
      fs.mkdirSync(dir, { recursive: true });
      const file = uniquePath(path.join(dir, `${sanitize(obs.status.game ? obs.status.game.name : 'Desktop')} ${new Date().toISOString().replace(/[:.]/g, '-')}.png`));
      await obs.screenshot(file);
      await new Promise((r) => setTimeout(r, 400));
      await library.addScreenshot(file, obs.status.game);
      notify('Screenshot saved', path.basename(file), '', 'shot');
    } catch (e) {
      notify('Screenshot failed', e.message, 'err', 'error');
    }
  },
  async toggleMic() {
    const muted = await obs.toggleMic();
    notify(muted ? 'Microphone muted' : 'Microphone live', '', muted ? 'err' : '', 'mark');
  },
  async toggleReplay() {
    await obs.toggleReplay();
    notify(obs.status.replayActive ? 'Replay buffer on' : 'Replay buffer paused', '', '', 'mark');
  }
};

function formatSecs(s) {
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, '0')}`;
}

// ---------------- media protocol (range-capable local file streaming) ----------------
const MIME = { '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime', '.mkv': 'video/x-matroska', '.webm': 'video/webm', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif', '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.ogg': 'audio/ogg', '.m4a': 'audio/mp4', '.flv': 'video/x-flv', '.ts': 'video/mp2t', '.bmp': 'image/bmp', '.avi': 'video/x-msvideo' };

function registerMediaProtocol() {
  protocol.handle('mtmedia', async (req) => {
    const url = new URL(req.url);
    const file = decodeURIComponent(url.pathname.replace(/^\//, ''));
    const ext = path.extname(file).toLowerCase();
    if (!MIME[ext]) return new Response('forbidden', { status: 403 });
    let stat;
    try { stat = await fs.promises.stat(file); } catch { return new Response('not found', { status: 404 }); }
    const range = req.headers.get('range');
    const headers = { 'Content-Type': MIME[ext], 'Accept-Ranges': 'bytes', 'Cache-Control': 'no-cache' };
    if (range) {
      const m = /bytes=(\d*)-(\d*)/.exec(range);
      let start = m && m[1] ? parseInt(m[1], 10) : 0;
      let end = m && m[2] ? parseInt(m[2], 10) : stat.size - 1;
      if (!m || !m[1]) { // suffix range
        const n = m && m[2] ? parseInt(m[2], 10) : 0;
        start = Math.max(0, stat.size - n);
        end = stat.size - 1;
      }
      end = Math.min(end, stat.size - 1);
      if (start > end) return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${stat.size}` } });
      const stream = fs.createReadStream(file, { start, end });
      return new Response(Readable.toWeb(stream), {
        status: 206,
        headers: { ...headers, 'Content-Range': `bytes ${start}-${end}/${stat.size}`, 'Content-Length': String(end - start + 1) }
      });
    }
    return new Response(Readable.toWeb(fs.createReadStream(file)), { status: 200, headers: { ...headers, 'Content-Length': String(stat.size) } });
  });
}

// ---------------- render jobs ----------------
function startJob(project, { outputPath, meta = {} } = {}) {
  const id = crypto.randomUUID();
  const s = settings.get();
  const fmt = (project.output && project.output.format) || 'mp4';
  const ext = fmt === 'gif' ? '.gif' : fmt === 'webm' ? '.webm' : fmt === 'mkv' ? '.mkv' : '.mp4';
  const dir = path.join(s.general.clipFolder, 'Exports');
  const out = outputPath || uniquePath(path.join(dir, `${sanitize(meta.title || 'Export')}${ext}`));
  const job = { id, title: meta.title || path.basename(out), output: out, progress: 0, phase: 'Starting', status: 'running', startedAt: Date.now() };
  jobs.set(id, job);
  const r = render(project, out, {
    fontPath: s.editor.fontPath,
    tmpRoot: app.getPath('temp'),
    // evaluated per ffmpeg pass: throttle while a game is running
    getRunOpts: () => {
      const gaming = !!obs.status.game;
      const low = settings.get().performance.lowPriorityJobs;
      return { lowPriority: low, threads: low && gaming ? Math.max(2, Math.floor(os.cpus().length / 3)) : 0 };
    },
    onProgress: (p, info = {}) => {
      if (p !== null && p !== undefined) job.progress = p;
      if (info.phase) job.phase = info.phase;
      if (info.speed) job.speed = info.speed;
      send('job:update', { ...job });
    }
  });
  job.cancel = r.cancel;
  send('job:update', { ...job, cancel: undefined });
  r.promise.then(async (res) => {
    job.status = 'done';
    job.progress = 1;
    job.size = res.size;
    job.result = res;
    let clip = null;
    if (meta.addToLibrary !== false) {
      const parent = meta.parentId ? library.get(meta.parentId) : null;
      clip = await library.add(out, {
        type: 'export',
        title: meta.title || path.basename(out, ext),
        game: parent ? parent.game : meta.game || null,
        parentId: meta.parentId || null
      });
      job.clipId = clip.id;
    }
    send('job:update', { ...job, cancel: undefined });
    notify(meta.kind === 'compress' ? 'Compression finished' : 'Export finished', `${path.basename(out)} · ${(res.size / 1e6).toFixed(1)} MB`, '', 'clip');
  }).catch((e) => {
    job.status = e.message === 'Cancelled' ? 'cancelled' : 'error';
    job.error = e.message;
    try { if (fs.existsSync(out)) fs.unlinkSync(out); } catch {}
    send('job:update', { ...job, cancel: undefined });
    if (job.status === 'error') notify('Export failed', e.message.split('\n')[0], 'err', 'error');
  });
  return { ...job, cancel: undefined };
}

// ---------------- IPC ----------------
function registerIpc() {
  const h = (ch, fn) => ipcMain.handle(ch, (_e, ...args) => fn(...args));

  h('app:info', () => ({ version: app.getVersion(), packaged: app.isPackaged, platform: process.platform, userData: app.getPath('userData') }));
  h('app:openExternal', (url) => /^https?:/.test(url) && shell.openExternal(url));
  h('app:openPath', (p) => shell.openPath(p));
  h('app:showItem', (p) => shell.showItemInFolder(p));
  h('app:quit', () => { quitting = true; app.quit(); });

  h('settings:get', () => settings.get());
  h('settings:defaults', () => settings.defaults());
  h('settings:update', async (patch) => {
    const next = settings.update(patch);
    if (patch.hotkeys) send('hotkeys:failed', hotkeys.register());
    if (patch.general && patch.general.launchOnStartup !== undefined) applyLoginItem();
    if (patch.autoClip) api.restart();
    if (patch.overlay && patch.overlay.position) overlay.reposition();
    if (patch.watchFolders) library.setWatchFolders(next.watchFolders);
    if (patch.storage) library.enforceStorage().catch(() => {});
    if (patch.general && patch.general.clipFolder) {
      fs.mkdirSync(next.general.clipFolder, { recursive: true });
      library.scan().catch(() => {});
    }
    queueRecorderUpdate(patch);
    return next;
  });
  h('hotkeys:pause', () => hotkeys.pause());
  h('hotkeys:resume', () => hotkeys.resume());
  h('hotkeys:failed', () => hotkeys.failed);

  h('recorder:status', () => obs.status);
  h('quality:plan', (patch) => {
    const merged = patch ? mergePatch(settings.get(), patch) : settings.get();
    return obs.computePlan(obs.profileFor(obs.status.game), merged);
  });
  h('perf:stats', async () => {
    const metrics = app.getAppMetrics();
    const appMB = Math.round(metrics.reduce((t, m) => t + (m.memory ? m.memory.workingSetSize : 0), 0) / 1024);
    const appCpu = Math.round(metrics.reduce((t, m) => t + (m.cpu ? m.cpu.percentCPUUsage : 0), 0) * 10) / 10;
    return { appMB, appCpu, totalMB: Math.round(os.totalmem() / 1048576), freeMB: Math.round(os.freemem() / 1048576), autoMemoryMB: autoMemoryMB(), cores: os.cpus().length };
  });
  h('recorder:clip', (seconds) => actions.clip(seconds));
  h('recorder:toggleRecording', () => actions.toggleRecording());
  h('recorder:toggleReplay', () => actions.toggleReplay());
  h('recorder:bookmark', () => actions.bookmark());
  h('recorder:screenshot', () => actions.screenshot());
  h('recorder:toggleMic', () => actions.toggleMic());
  h('recorder:restart', () => obs.restart());
  h('recorder:devices', () => obs.getDevices());
  h('recorder:hw', async () => ({ ffmpegEncoders: await hwEncoders(), gpus: await require('./obs/hardware').detectGpus() }));

  h('library:list', () => library.list());
  h('library:get', (id) => library.get(id));
  h('library:update', (id, patch) => library.update(id, patch));
  h('library:remove', (ids, opts) => library.remove(ids, opts));
  h('library:scan', () => library.scan());
  h('library:stats', () => library.storageStats());
  h('library:cleanup', () => library.enforceStorage());
  h('library:import', async (files, opts) => {
    if (!files) {
      const r = await dialog.showOpenDialog(mainWindow, {
        title: 'Import videos', properties: ['openFile', 'multiSelections'],
        filters: [{ name: 'Media', extensions: ['mp4', 'mkv', 'mov', 'webm', 'avi', 'flv', 'ts', 'm4v', 'png', 'jpg', 'jpeg'] }]
      });
      if (r.canceled) return [];
      files = r.filePaths;
    }
    return library.importFiles(files, opts);
  });
  h('library:copyToClipboard', (id) => {
    const c = library.get(id);
    if (!c) return false;
    return new Promise((resolve) => {
      execFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', `Set-Clipboard -LiteralPath '${c.path.replace(/'/g, "''")}'`], { windowsHide: true }, (err) => resolve(!err));
    });
  });
  h('albums:list', () => library.albums());
  h('albums:create', (name) => library.createAlbum(name));
  h('albums:rename', (id, name) => library.renameAlbum(id, name));
  h('albums:delete', (id) => library.deleteAlbum(id));
  h('albums:add', (albumId, ids) => library.addToAlbum(albumId, ids));
  h('albums:remove', (albumId, ids) => library.removeFromAlbum(albumId, ids));

  h('media:probe', (p) => probe(p));
  h('media:extractTracks', (p) => extractTracks(p));
  h('media:filmstrip', (p, d) => filmstrip(p, d));
  h('media:estimate', (project) => estimate(project));
  h('media:render', (project, opts) => startJob(project, opts));
  h('media:cancel', (id) => { const j = jobs.get(id); if (j && j.cancel) j.cancel(); });
  h('media:jobs', () => [...jobs.values()].map((j) => ({ ...j, cancel: undefined })));
  h('media:clearJobs', () => { for (const [id, j] of jobs) if (j.status !== 'running') jobs.delete(id); });

  h('ff:capture', (spec) => findFrame.capture(spec));
  h('ff:analyze', (ref, over) => findFrame.analyze(ref, over));
  h('ff:finders', () => findFrame.finders());
  h('ff:createFinder', (data) => findFrame.createFinder(data));
  h('ff:updateFinder', (id, patch) => findFrame.updateFinder(id, patch));
  h('ff:deleteFinder', (id) => findFrame.deleteFinder(id));
  h('ff:results', (id) => findFrame.results(id));
  h('ff:clearResults', (id) => findFrame.clearResults(id));
  h('ff:removeMatch', (fid, clipId, t) => findFrame.removeMatch(fid, clipId, t));
  h('ff:search', (spec) => findFrame.search(spec));
  h('ff:jobs', () => findFrame.listJobs());
  h('ff:cancel', (id) => findFrame.cancel(id));
  h('ff:clearJobs', () => findFrame.clearJobs());

  h('games:list', () => {
    const s = settings.get().games;
    return { builtin: builtinGames, custom: s.custom, profiles: s.profiles, disabled: s.disabled || [], current: obs.status.game };
  });
  h('games:addCustom', async () => {
    const r = await dialog.showOpenDialog(mainWindow, { title: 'Select game executable', properties: ['openFile'], filters: [{ name: 'Programs', extensions: ['exe'] }] });
    if (r.canceled) return null;
    const exe = path.basename(r.filePaths[0]);
    const name = path.basename(exe, '.exe');
    const g = { id: `custom-${name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`, name, exe: [exe], path: r.filePaths[0] };
    const custom = settings.get().games.custom.filter((x) => x.id !== g.id).concat(g);
    settings.update({ games: { custom } });
    return g;
  });

  h('dialog:pickFolder', async (title) => {
    const r = await dialog.showOpenDialog(mainWindow, { title: title || 'Choose folder', properties: ['openDirectory', 'createDirectory'] });
    return r.canceled ? null : r.filePaths[0];
  });
  h('dialog:pickFile', async (opts = {}) => {
    const r = await dialog.showOpenDialog(mainWindow, { title: opts.title, properties: ['openFile'], filters: opts.filters });
    return r.canceled ? null : r.filePaths[0];
  });
  h('dialog:saveFile', async (opts = {}) => {
    const r = await dialog.showSaveDialog(mainWindow, { title: opts.title, defaultPath: opts.defaultPath, filters: opts.filters });
    return r.canceled ? null : r.filePath;
  });

  h('updater:status', () => updater.state);
  h('updater:check', () => updater.check());
  h('updater:download', () => updater.download());
  h('updater:install', () => { quitting = true; updater.install(); });
}

// Debounce recorder reconfiguration so dragging a slider doesn't restart OBS
// on every tick.
let pendingPatch = null;
let pendingTimer = null;
function mergePatch(a, b) {
  const out = { ...(a || {}) };
  for (const k of Object.keys(b)) {
    out[k] = b[k] && typeof b[k] === 'object' && !Array.isArray(b[k]) ? mergePatch(out[k], b[k]) : b[k];
  }
  return out;
}
function queueRecorderUpdate(patch) {
  const relevant = ['recording', 'quality', 'audio', 'hotkeys', 'autoClip', 'games', 'general', 'advanced'];
  if (!Object.keys(patch).some((k) => relevant.includes(k))) return;
  pendingPatch = mergePatch(pendingPatch, patch);
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(() => {
    const p = pendingPatch;
    pendingPatch = null;
    obs.onSettingsChanged(p).catch((e) => console.error(e));
  }, 1200);
}

// Extract each audio track of a multi-track clip so the editor can preview
// tracks independently (cached by path + mtime).
async function extractTracks(file) {
  const info = await probe(file);
  if (info.audioTracks.length < 2) return [];
  const st = fs.statSync(file);
  const key = crypto.createHash('sha1').update(`${file}|${st.mtimeMs}|${st.size}`).digest('hex').slice(0, 16);
  const dir = path.join(app.getPath('userData'), 'cache', 'tracks', key);
  fs.mkdirSync(dir, { recursive: true });
  const outs = info.audioTracks.map((_, i) => path.join(dir, `track${i}.m4a`));
  if (outs.every((o) => fs.existsSync(o))) return outs;
  const args = ['-i', file];
  info.audioTracks.forEach((t, i) => args.push('-map', `0:a:${i}`, '-c', t.codec === 'aac' ? 'copy' : 'aac', '-vn', outs[i]));
  await run(args).promise;
  return outs;
}

async function filmstrip(file, duration) {
  const st = fs.statSync(file);
  const key = crypto.createHash('sha1').update(`${file}|${st.mtimeMs}|${st.size}`).digest('hex').slice(0, 16);
  const dir = path.join(app.getPath('userData'), 'cache', 'film', key);
  const done = path.join(dir, '.done');
  if (fs.existsSync(done)) return fs.readdirSync(dir).filter((f) => f.endsWith('.jpg')).sort().map((f) => path.join(dir, f));
  fs.mkdirSync(dir, { recursive: true });
  const count = Math.max(4, Math.min(80, Math.round((duration || 60) / 2)));
  const interval = Math.max(0.5, (duration || 60) / count);
  await run(['-skip_frame', 'nokey', '-i', file, '-an', '-vf', `fps=1/${interval.toFixed(3)},scale=160:-2`, '-fps_mode', 'vfr', '-q:v', '7', path.join(dir, 'f_%04d.jpg')]).promise;
  fs.writeFileSync(done, '1');
  return fs.readdirSync(dir).filter((f) => f.endsWith('.jpg')).sort().map((f) => path.join(dir, f));
}

function pruneCache() {
  // keep the cache from growing forever: drop entries older than 14 days
  const root = path.join(app.getPath('userData'), 'cache');
  for (const sub of ['tracks', 'film']) {
    const d = path.join(root, sub);
    let entries = [];
    try { entries = fs.readdirSync(d); } catch { continue; }
    for (const e of entries) {
      const p = path.join(d, e);
      try { if (Date.now() - fs.statSync(p).mtimeMs > 14 * 864e5) fs.rmSync(p, { recursive: true, force: true }); } catch {}
    }
  }
}

function applyLoginItem() {
  if (!app.isPackaged) return;
  app.setLoginItemSettings({ openAtLogin: settings.get().general.launchOnStartup, args: ['--hidden'] });
}

// ---------------- boot ----------------
app.on('second-instance', () => showWindow());

app.whenReady().then(async () => {
  settings = new Settings();
  library = new Library({ settings, userData: app.getPath('userData') });
  overlay = new Overlay(settings);
  updater = new Updater(settings);
  const displaySize = () => {
    const d = screen.getPrimaryDisplay();
    return { width: Math.round(d.size.width * d.scaleFactor / 2) * 2, height: Math.round(d.size.height * d.scaleFactor / 2) * 2 };
  };
  obs = new ObsManager({
    settings,
    userData: app.getPath('userData'),
    appRoot: path.join(__dirname, '..'),
    isPackaged: app.isPackaged,
    getDisplaySize: displaySize,
    getMonitorSize: (i) => {
      const d = screen.getAllDisplays()[i] || screen.getPrimaryDisplay();
      return { width: Math.round(d.size.width * d.scaleFactor / 2) * 2, height: Math.round(d.size.height * d.scaleFactor / 2) * 2 };
    }
  });
  detector = new GameDetector(settings);
  findFrame = new FindFrame({
    settings, library, userData: app.getPath('userData'), appRoot: path.join(__dirname, '..'),
    isPackaged: app.isPackaged, isGaming: () => !!obs.status.game
  });
  findFrame.on('job', (j) => send('ff:job', j));
  findFrame.on('match', (m) => send('ff:match', m));
  findFrame.on('thumb', (t) => send('ff:thumb', t));
  findFrame.on('changed', () => send('ff:changed'));
  hotkeys = new Hotkeys(settings, actions);

  registerMediaProtocol();
  registerIpc();
  // Starting to tray: don't even create the UI until it's opened.
  if (!(startHidden || settings.get().general.startMinimized) || process.env.MT_SNAPSHOT_DIR) createWindow();

  tray = new Tray(trayIcon());
  tray.on('double-click', () => showWindow());
  updateTray();

  obs.on('status', (st) => { send('recorder:status', st); updateTray(); });
  obs.on('replay-saved', async (info) => {
    try {
      const clip = await library.ingestReplay(info);
      send('clip:new', clip);
    } catch (e) {
      notify('Clip processing failed', e.message, 'err', 'error');
    }
  });
  obs.on('recording-finished', async (info) => {
    try {
      const clip = await library.ingestRecording(info);
      send('clip:new', clip);
      if (Notification.isSupported() && (!mainWindow || !mainWindow.isVisible())) {
        new Notification({ title: 'Session saved', body: `${clip.title} · ${info.bookmarks.length} bookmark(s)` }).show();
      }
    } catch (e) {
      notify('Session processing failed', e.message, 'err', 'error');
    }
  });
  library.on('changed', () => send('library:changed'));
  obs.on('adapt', ({ from, to, encoderLagPct }) => {
    settings.update({ performance: { encoderPreset: to } });
    queueRecorderUpdate({ performance: { encoderPreset: to } });
    notify('Recording optimized', `Encoder was dropping ${encoderLagPct}% of frames — switched ${from} → ${to}`, '', 'mark');
  });
  obs.on('warn', ({ renderLagPct }) => {
    notify('Your GPU is overloaded', `${renderLagPct}% of frames skipped. Cap your game's FPS or lower the max recording resolution.`, 'err', null);
  });
  library.on('cleanup', (n) => notify('Storage cleanup', `Removed ${n} old clip(s) to stay under your limit`, '', 'mark'));
  updater.on('status', (st) => send('updater:status', st));
  detector.on('change', (game) => {
    obs.setGame(game).catch((e) => console.error(e));
    if (game) notify(`${game.name} detected`, obs.status.replayActive || settings.get().recording.replayEnabled ? `Press ${settings.get().hotkeys.clips[0]?.accelerator || 'F8'} to clip` : 'Replay buffer is off', '', null);
  });

  api = startApiServer({
    settings,
    getStatus: () => ({ ...obs.status }),
    onBookmark: async (label) => actions.bookmark(label),
    onClip: async ({ event, before, after }) => {
      if (obs.status.recording) {
        const bm = obs.addBookmark(event);
        notify(`${event}`, 'Bookmarked in session', 'mark', 'mark');
        return { ok: true, bookmark: bm };
      }
      const raw = await obs.saveClip({ seconds: before + after, delayMs: after * 1000, source: 'auto', event, label: event });
      notify(`Auto clip: ${event}`, obs.status.game ? obs.status.game.name : '', '', 'clip');
      return { ok: true, raw };
    }
  });

  send('hotkeys:failed', hotkeys.register());
  applyLoginItem();
  library.setWatchFolders(settings.get().watchFolders);
  library.scan().catch(() => {});
  detector.start();
  obs.start();
  updater.startSchedule();
  setTimeout(pruneCache, 60000);
  if (process.env.MT_SNAPSHOT_DIR && !app.isPackaged) snapshotPages(process.env.MT_SNAPSHOT_DIR);
});

// Dev-only: render each page and save screenshots + console errors (UI smoke test).
async function snapshotPages(dir) {
  fs.mkdirSync(dir, { recursive: true });
  const errors = [];
  mainWindow.webContents.on('console-message', (_e, level, message) => { if (level >= 2) errors.push(message); });
  mainWindow.show();
  await new Promise((r) => setTimeout(r, 6000));
  if (process.env.MT_SNAPSHOT_PRE) {
    await mainWindow.webContents.executeJavaScript(process.env.MT_SNAPSHOT_PRE).catch((e) => errors.push('pre: ' + e.message));
    await new Promise((r) => setTimeout(r, Number(process.env.MT_SNAPSHOT_WAIT || 8000)));
  }
  const first = library.list()[0];
  const routes = process.env.MT_SNAPSHOT_ROUTES ? process.env.MT_SNAPSHOT_ROUTES.split(',') : ['/home', '/library', '/compressor', '/games', '/settings', '/settings/performance', ...(first ? [`/clip/${first.id}`, `/editor/${first.id}`, `/compress/${first.id}`] : [])];
  for (let r of routes) {
    if (first) r = r.replace('{first}', first.id);
    await mainWindow.webContents.executeJavaScript(`location.hash = ${JSON.stringify(r)}`);
    await new Promise((res) => setTimeout(res, 2500));
    const img = await mainWindow.webContents.capturePage();
    fs.writeFileSync(path.join(dir, r.replace(/[^a-z0-9]+/gi, '_') + '.png'), img.toPNG());
  }
  fs.writeFileSync(path.join(dir, 'errors.txt'), errors.join('\n'));
  quitting = true;
  app.quit();
}

app.on('window-all-closed', (e) => {
  // keep running in tray
});

app.on('before-quit', async (e) => {
  if (app.mtCleanedUp) return;
  e.preventDefault();
  quitting = true;
  app.mtCleanedUp = true;
  try {
    hotkeys && hotkeys.dispose();
    detector && detector.stop();
    api && api.close();
    for (const j of jobs.values()) if (j.status === 'running' && j.cancel) j.cancel();
    findFrame && findFrame.cancelAll();
    findFrame && findFrame.flush();
    library && library.flush();
    await Promise.race([obs ? obs.stop() : Promise.resolve(), new Promise((r) => setTimeout(r, 12000))]);
  } finally {
    app.quit();
  }
});
