// Drives a bundled OBS Studio instance (portable mode, hidden in tray) over
// obs-websocket v5. OBS provides capture, encoding (NVENC/AMF/QSV/x264), the
// replay buffer and full-session recording; Mettaloid provides everything else.
const { spawn, execFile } = require('child_process');
const crypto = require('crypto');
const EventEmitter = require('events');
const fs = require('fs');
const net = require('net');
const path = require('path');
const wsMod = require('obs-websocket-js');
const OBSWebSocket = wsMod.OBSWebSocket || wsMod.default || wsMod;
const os = require('os');
const { writeObsConfig, trackMask, PROFILE } = require('./config-writer');
const { planQuality } = require('../quality-planner');
const { detectEncoderVendor, fileVersion, isWritable } = require('./hardware');

const SCENE = 'Mettaloid';
const IN_GAME = 'MT Game Capture';
const IN_DISPLAY = 'MT Display Capture';
const IN_GAME_AUDIO = 'MT Game Audio';
const IN_DISCORD = 'MT Discord';
const IN_DESKTOP = 'MT Desktop Audio';
const IN_MIC = 'MT Microphone';
const NOISE_FILTER = 'MT Noise Suppression';
const MAX_BUFFER = 1200; // 20 minutes
const PROFILE_B = 'Mettaloid B'; // alternate profile used for hot reconfiguration
const PRIORITY = { High: os.constants.priority.PRIORITY_HIGH, AboveNormal: os.constants.priority.PRIORITY_ABOVE_NORMAL, Normal: 0, BelowNormal: os.constants.priority.PRIORITY_BELOW_NORMAL };
const PRESET_ORDER = ['quality', 'balanced', 'performance', 'max-performance'];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function freePort() {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
    srv.on('error', () => resolve(4466));
  });
}

function tracks(list) {
  const t = {};
  for (let i = 1; i <= 6; i++) t[String(i)] = list.includes(i);
  return t;
}

class ObsManager extends EventEmitter {
  /**
   * @param {object} o
   * @param {import('../settings').Settings} o.settings
   * @param {string} o.userData
   * @param {string} o.appRoot
   * @param {boolean} o.isPackaged
   * @param {() => {width:number,height:number}} o.getDisplaySize
   * @param {(i:number) => {width:number,height:number}} o.getMonitorSize
   */
  constructor(o) {
    super();
    this.o = o;
    this.settings = o.settings;
    this.obs = new OBSWebSocket();
    this.proc = null;
    this.stopping = false;
    this.crashTimes = [];
    this.pendingSaves = [];
    this.bookmarks = [];
    this.sessionMeta = null;
    this.windowRetry = null;
    this.status = {
      obsState: 'idle', // idle|preparing|starting|ready|error|missing
      obsVersion: null,
      error: null,
      replayActive: false,
      recording: false,
      recordStartedAt: null,
      game: null,
      captureMode: 'game',
      micMuted: !this.settings.get().audio.micEnabled,
      bufferSeconds: this.bufferSeconds(),
      encoder: null,
      vendor: null,
      outputSize: null,
      capturingWindow: null,
      plan: null,
      stats: null
    };
    this.statSamples = [];
    this.stagingDir = path.join(this.settings.get().general.clipFolder, '.staging');
    this.wireEvents();
  }

  // ---------- status ----------
  setStatus(patch) {
    Object.assign(this.status, patch);
    this.emit('status', { ...this.status });
  }

  bufferSeconds(profile, s = this.settings.get()) {
    const base = (profile && profile.replaySeconds) || s.recording.replaySeconds;
    const hk = Math.max(0, ...s.hotkeys.clips.map((c) => Number(c.seconds) || 0));
    const auto = s.autoClip.enabled ? s.autoClip.secondsBefore + s.autoClip.secondsAfter : 0;
    return Math.min(MAX_BUFFER, Math.max(5, base, hk, auto));
  }

  // ---------- locating / preparing OBS ----------
  candidateRoots() {
    const { appRoot, isPackaged, userData } = this.o;
    const list = [];
    const custom = this.settings.get().advanced && this.settings.get().advanced.obsPath;
    if (custom) list.push(custom);
    list.push(isPackaged ? path.join(process.resourcesPath, 'obs') : path.join(appRoot, 'vendor', 'obs'));
    list.push(path.join(userData, 'obs-runtime'));
    list.push(path.join(process.env.ProgramFiles || 'C:\\Program Files', 'obs-studio'));
    return list;
  }

  locate() {
    for (const root of this.candidateRoots()) {
      if (fs.existsSync(path.join(root, 'bin', '64bit', 'obs64.exe'))) return root;
    }
    return null;
  }

  async prepareRuntime(root) {
    if (isWritable(root)) return root;
    // Portable mode needs a writable folder; mirror OBS into userData once per version.
    const dest = path.join(this.o.userData, 'obs-runtime');
    const srcExe = path.join(root, 'bin', '64bit', 'obs64.exe');
    const stampFile = path.join(dest, '.mt-source');
    const stamp = `${srcExe}|${fs.statSync(srcExe).mtimeMs}`;
    let current = '';
    try { current = fs.readFileSync(stampFile, 'utf8'); } catch {}
    if (current !== stamp) {
      this.setStatus({ obsState: 'preparing' });
      fs.rmSync(dest, { recursive: true, force: true });
      await fs.promises.cp(root, dest, {
        recursive: true,
        filter: (src) => !src.startsWith(path.join(root, 'config'))
      });
      fs.writeFileSync(stampFile, stamp);
    }
    return dest;
  }

  killOrphan() {
    const pidFile = path.join(this.o.userData, 'obs.pid');
    try {
      const pid = parseInt(fs.readFileSync(pidFile, 'utf8'), 10);
      if (pid) {
        return new Promise((resolve) => {
          execFile('tasklist', ['/FI', `PID eq ${pid}`, '/FO', 'CSV', '/NH'], { windowsHide: true }, (err, out) => {
            if (!err && /obs64\.exe/i.test(String(out))) {
              try { process.kill(pid); } catch {}
            }
            resolve();
          });
        });
      }
    } catch {}
    return Promise.resolve();
  }

  // ---------- lifecycle ----------
  async start() {
    this.stopping = false;
    const found = this.locate();
    if (!found) {
      this.setStatus({ obsState: 'missing', error: 'OBS Studio was not found. Run "npm run fetch-obs" or install OBS Studio.' });
      return;
    }
    try {
      await this.killOrphan();
      const root = await this.prepareRuntime(found);
      this.root = root;
      const exe = path.join(root, 'bin', '64bit', 'obs64.exe');
      this.setStatus({ obsState: 'starting', error: null });
      this.encoderVerified = false;
      this.replayState = null;
      const [obsVersion, detectedVendor] = await Promise.all([fileVersion(exe), detectEncoderVendor()]);
      this.obsVersion = obsVersion;
      this.detectedVendor = detectedVendor;
      this.port = await freePort();
      this.password = crypto.randomBytes(16).toString('hex');
      const plan = this.computePlan(this.profileFor(this.lastGame));
      // Write both profiles: OBS only knows profiles that exist at startup, and
      // later changes are applied by switching to the inactive one (no restart).
      this.writeConfig(PROFILE_B, plan);
      this.writeConfig(PROFILE, plan);
      this.activeProfile = PROFILE;
      this.currentProfile = PROFILE;
      this.appliedSig = this.signature(plan);
      this.setStatus({ obsVersion });
      this.launch(exe);
      await this.connect();
      await this.setupScene();
      this.applyPriority();
      this.startStats();
      this.setStatus({ obsState: 'ready' });
      this.emit('ready');
      await this.setGame(this.lastGame || null);
    } catch (err) {
      this.setStatus({ obsState: 'error', error: String(err && err.message ? err.message : err) });
    }
  }

  // ---------- quality plan / config ----------
  resolveVendor() {
    const q = this.settings.get().quality;
    if (q.encoder !== 'auto') return q.encoder;
    return this.forceX264 ? 'x264' : this.detectedVendor || 'x264';
  }

  computePlan(profile, s = this.settings.get()) {
    const perf = s.performance;
    const resolution = profile && profile.resolution ? String(profile.resolution) : null;
    const fps = profile && profile.fps ? Number(profile.fps) : null;
    return planQuality({
      memoryMB: perf.replayMemoryMB,
      seconds: this.bufferSeconds(profile, s),
      native: this.baseSize(s),
      maxResolution: resolution || perf.maxResolution,
      maxFps: fps || perf.maxFps,
      codec: s.quality.codec,
      audioTracks: s.audio.separateDiscordTrack ? 4 : 3,
      audioKbps: s.audio.audioBitrate,
      auto: perf.autoQuality,
      manual: { resolution: resolution || s.quality.resolution, fps: fps || s.quality.fps, bitrateKbps: s.quality.bitrateKbps, rateControl: s.quality.rateControl }
    });
  }

  // Everything that ends up in the OBS profile; a change means reconfigure.
  signature(plan) {
    const s = this.settings.get();
    return JSON.stringify({
      p: [plan.width, plan.height, plan.fps, plan.videoKbps, plan.audioKbps, plan.memoryMB, plan.seconds, plan.auto],
      v: this.resolveVendor(), q: s.quality, f: s.recording.fileFormat, t: trackMask(s.audio), pr: s.performance.encoderPreset,
      st: s.general.clipFolder, b: this.baseSize()
    });
  }

  writeConfig(profileName, plan) {
    const s = this.settings.get();
    this.stagingDir = path.join(s.general.clipFolder, '.staging');
    const cfg = writeObsConfig({
      configRoot: path.join(this.root, 'config', 'obs-studio'),
      settings: s,
      profileName,
      stagingDir: this.stagingDir,
      baseSize: this.baseSize(),
      vendor: this.resolveVendor(),
      obsVersion: this.obsVersion,
      wsPort: this.port,
      wsPassword: this.password,
      replaySeconds: plan.seconds,
      memoryMB: plan.memoryMB,
      plan: plan.auto ? plan : null
    });
    this.setStatus({ vendor: this.resolveVendor(), encoder: cfg.encoderId, outputSize: cfg.outputSize, plan, bufferSeconds: plan.seconds });
    return cfg;
  }

  /**
   * Apply the current plan. Writes the new settings into the inactive OBS
   * profile and switches to it, so OBS reloads encoder/video/replay settings
   * in about a second instead of a full restart.
   */
  async reconfigure(profile) {
    const plan = this.computePlan(profile);
    const sig = this.signature(plan);
    if (sig === this.appliedSig) {
      this.setStatus({ plan });
      return false;
    }
    if (this.status.recording) {
      // outputs can't change mid-recording; apply when it stops
      this.pendingReconfig = true;
      return false;
    }
    await this.stopOutputsForReconfig();
    const next = this.activeProfile === PROFILE ? PROFILE_B : PROFILE;
    this.writeConfig(next, plan);
    try {
      await this.call('SetCurrentProfile', { profileName: next });
      const ok = await this.waitFor(() => this.currentProfile === next, 5000);
      if (!ok) {
        const { currentProfileName } = await this.call('GetProfileList');
        if (currentProfileName !== next) throw new Error('profile did not switch');
      }
      this.activeProfile = next;
      this.appliedSig = sig;
      await this.fitToCanvas(IN_GAME);
      await this.fitToCanvas(IN_DISPLAY);
      return true;
    } catch (e) {
      this.emit('log', `hot reconfigure failed (${e.message}); restarting OBS`);
      setTimeout(() => this.restart(), 0);
      return false;
    }
  }

  applyPriority() {
    const pr = this.settings.get().performance.obsPriority;
    if (this.proc && PRIORITY[pr] !== undefined) {
      try { os.setPriority(this.proc.pid, PRIORITY[pr]); } catch {}
    }
  }

  // ---------- live stats + adaptive encoder preset ----------
  startStats() {
    clearInterval(this.statsTimer);
    this.statSamples = [];
    this.lastStats = null;
    this.statsTimer = setInterval(() => this.pollStats().catch(() => {}), 3000);
  }

  async pollStats() {
    if (this.status.obsState !== 'ready') return;
    const st = await this.call('GetStats');
    const prev = this.lastStats;
    this.lastStats = st;
    if (!prev) return;
    const d = (k) => Math.max(0, st[k] - prev[k]);
    this.statSamples.push({ out: d('outputTotalFrames'), outSkip: d('outputSkippedFrames'), ren: d('renderTotalFrames'), renSkip: d('renderSkippedFrames') });
    if (this.statSamples.length > 10) this.statSamples.shift(); // ~30 s window
    const sum = (k) => this.statSamples.reduce((t, x) => t + x[k], 0);
    const encLag = sum('out') ? sum('outSkip') / sum('out') : 0;
    const renderLag = sum('ren') ? sum('renSkip') / sum('ren') : 0;
    this.setStatus({
      stats: {
        cpu: Math.round(st.cpuUsage * 10) / 10,
        memoryMB: Math.round(st.memoryUsage),
        fps: Math.round(st.activeFps),
        renderMs: Math.round(st.averageFrameRenderTime * 100) / 100,
        encoderLagPct: Math.round(encLag * 1000) / 10,
        renderLagPct: Math.round(renderLag * 1000) / 10
      }
    });
    const perf = this.settings.get().performance;
    const now = Date.now();
    // Encoder can't keep up → step the encoder preset down (persisted by main).
    if (sum('out') >= 600 && encLag > 0.02 && this.status.replayActive && perf.adaptive && now - (this.lastAdapt || 0) > 60000) {
      const i = PRESET_ORDER.indexOf(perf.encoderPreset);
      if (i >= 0 && i < PRESET_ORDER.length - 1) {
        this.lastAdapt = now;
        this.statSamples = [];
        this.emit('adapt', { from: perf.encoderPreset, to: PRESET_ORDER[i + 1], encoderLagPct: Math.round(encLag * 1000) / 10 });
      }
    }
    // GPU too busy to render frames → encoder preset won't help; warn the user.
    if (sum('ren') >= 600 && renderLag > 0.05 && now - (this.lastRenderWarn || 0) > 300000) {
      this.lastRenderWarn = now;
      this.emit('warn', { kind: 'render', renderLagPct: Math.round(renderLag * 1000) / 10 });
    }
  }

  baseSize(s = this.settings.get()) {
    if (s.recording.captureMode === 'desktop') return this.o.getMonitorSize(s.recording.monitorIndex);
    return this.o.getDisplaySize();
  }

  launch(exe) {
    const args = [
      '--portable', '--multi', '--minimize-to-tray', '--disable-updater',
      '--disable-shutdown-check', '--disable-missing-files-check',
      '--profile', 'Mettaloid', '--collection', 'Mettaloid', '--scene', SCENE
    ];
    const proc = spawn(exe, args, { cwd: path.dirname(exe), windowsHide: true, detached: false, stdio: 'ignore' });
    this.proc = proc;
    setTimeout(() => this.applyPriority(), 3000);
    try { fs.writeFileSync(path.join(this.o.userData, 'obs.pid'), String(proc.pid)); } catch {}
    proc.on('exit', (code) => {
      if (this.proc !== proc) return;
      this.proc = null;
      this.setStatus({ replayActive: false, recording: false });
      if (this.stopping) return;
      // crash recovery: restart up to 3 times per minute
      const now = Date.now();
      this.crashTimes = this.crashTimes.filter((t) => now - t < 60000);
      this.crashTimes.push(now);
      if (this.crashTimes.length <= 3) {
        this.setStatus({ obsState: 'starting', error: `OBS exited (code ${code}); restarting…` });
        setTimeout(() => this.start(), 1500);
      } else {
        this.setStatus({ obsState: 'error', error: 'OBS keeps crashing. Check Settings → Quality (try the x264 encoder).' });
      }
    });
  }

  async connect() {
    const deadline = Date.now() + 45000;
    let lastErr;
    while (Date.now() < deadline) {
      if (!this.proc) throw new Error('OBS exited during startup');
      try {
        await this.obs.connect(`ws://127.0.0.1:${this.port}`, this.password, { rpcVersion: 1 });
        // The websocket accepts connections before OBS has finished loading
        // (requests fail with NotReady) - wait until it answers.
        while (Date.now() < deadline) {
          try {
            await this.obs.call('GetSceneList');
            return;
          } catch {
            await sleep(400);
          }
        }
        throw new Error('OBS did not become ready');
      } catch (e) {
        lastErr = e;
        await sleep(700);
      }
    }
    throw new Error(`Could not connect to OBS websocket: ${lastErr && lastErr.message}`);
  }

  async stop() {
    this.stopping = true;
    clearInterval(this.windowRetry);
    clearInterval(this.statsTimer);
    this.lastStats = null;
    try {
      if (this.status.recording) {
        await this.obs.call('StopRecord').catch(() => {});
        await this.waitFor(() => !this.status.recording, 10000);
      }
      await this.obs.disconnect().catch(() => {});
    } catch {}
    const proc = this.proc;
    if (proc) {
      await new Promise((resolve) => {
        const t = setTimeout(() => { try { proc.kill(); } catch {} resolve(); }, 5000);
        proc.once('exit', () => { clearTimeout(t); resolve(); });
        // graceful close (WM_CLOSE)
        execFile('taskkill', ['/PID', String(proc.pid)], { windowsHide: true }, () => {});
      });
    }
    this.proc = null;
    this.setStatus({ obsState: 'idle', replayActive: false, recording: false });
  }

  async restart() {
    await this.stop();
    await this.start();
  }

  waitFor(pred, timeout = 8000) {
    return new Promise((resolve) => {
      if (pred()) return resolve(true);
      const start = Date.now();
      const iv = setInterval(() => {
        if (pred()) { clearInterval(iv); resolve(true); }
        else if (Date.now() - start > timeout) { clearInterval(iv); resolve(false); }
      }, 100);
    });
  }

  call(name, data) {
    return this.obs.call(name, data);
  }

  // ---------- websocket events ----------
  wireEvents() {
    this.obs.on('ConnectionClosed', () => {
      if (!this.stopping && this.status.obsState === 'ready') this.setStatus({ obsState: 'starting' });
    });
    this.obs.on('ReplayBufferStateChanged', ({ outputActive, outputState }) => {
      this.replayState = outputState;
      if (outputState === 'OBS_WEBSOCKET_OUTPUT_STARTED') this.encoderVerified = true;
      this.setStatus({ replayActive: outputState === 'OBS_WEBSOCKET_OUTPUT_STOPPED' ? false : outputActive || outputState === 'OBS_WEBSOCKET_OUTPUT_STOPPING' });
    });
    this.obs.on('ReplayBufferSaved', ({ savedReplayPath }) => this.onReplaySaved(savedReplayPath));
    this.obs.on('CurrentProfileChanged', ({ profileName }) => { this.currentProfile = profileName; });
    this.obs.on('RecordStateChanged', ({ outputActive, outputState, outputPath }) => {
      if (outputState === 'OBS_WEBSOCKET_OUTPUT_STARTED') {
        this.bookmarks = [];
        this.sessionMeta = { game: this.status.game, startedAt: Date.now() };
        this.setStatus({ recording: true, recordStartedAt: Date.now() });
      } else if (outputState === 'OBS_WEBSOCKET_OUTPUT_STOPPED') {
        const meta = this.sessionMeta || {};
        this.setStatus({ recording: false, recordStartedAt: null });
        if (outputPath) {
          this.emit('recording-finished', {
            path: outputPath,
            bookmarks: this.bookmarks.slice(),
            game: meta.game || null,
            startedAt: meta.startedAt || Date.now()
          });
        }
        this.bookmarks = [];
        this.sessionMeta = null;
        if (this.pendingReconfig) {
          this.pendingReconfig = false;
          setTimeout(() => this.setGame(this.status.game).catch(() => {}), 500);
        }
      } else {
        this.setStatus({ recording: outputActive });
      }
    });
  }

  onReplaySaved(p) {
    const pending = this.pendingSaves.shift();
    if (!pending) {
      // Saved from OBS directly (shouldn't happen, OBS is hidden) – still import.
      this.emit('replay-saved', { path: p, seconds: null, game: this.status.game, source: 'obs' });
      return;
    }
    clearTimeout(pending.timer);
    this.emit('replay-saved', { path: p, ...pending.opts, game: this.status.game, bufferSeconds: this.status.bufferSeconds });
    pending.resolve(p);
  }

  // ---------- scene / sources ----------
  async inputNames() {
    const { inputs } = await this.call('GetInputList');
    return new Set(inputs.map((i) => i.inputName));
  }

  async ensureInput(existing, name, kind, settings, enabled = true) {
    if (existing.has(name)) return false;
    await this.call('CreateInput', { sceneName: SCENE, inputName: name, inputKind: kind, inputSettings: settings, sceneItemEnabled: enabled });
    existing.add(name);
    return true;
  }

  async sceneItemId(name) {
    const { sceneItemId } = await this.call('GetSceneItemId', { sceneName: SCENE, sourceName: name });
    return sceneItemId;
  }

  async setItemEnabled(name, enabled) {
    try {
      const id = await this.sceneItemId(name);
      await this.call('SetSceneItemEnabled', { sceneName: SCENE, sceneItemId: id, sceneItemEnabled: enabled });
    } catch {}
  }

  async fitToCanvas(name) {
    try {
      const v = await this.call('GetVideoSettings');
      const id = await this.sceneItemId(name);
      await this.call('SetSceneItemTransform', {
        sceneName: SCENE,
        sceneItemId: id,
        sceneItemTransform: {
          positionX: 0, positionY: 0, alignment: 5,
          boundsType: 'OBS_BOUNDS_SCALE_INNER', boundsAlignment: 0,
          boundsWidth: v.baseWidth, boundsHeight: v.baseHeight
        }
      });
    } catch {}
  }

  async setupScene() {
    const { scenes } = await this.call('GetSceneList');
    if (!scenes.find((s) => s.sceneName === SCENE)) await this.call('CreateScene', { sceneName: SCENE });
    await this.call('SetCurrentProgramScene', { sceneName: SCENE });
    const existing = await this.inputNames();
    await this.ensureInput(existing, IN_GAME, 'game_capture', {
      capture_mode: 'any_fullscreen', capture_cursor: false, capture_overlays: false, anti_cheat_hook: true, limit_framerate: false
    });
    await this.ensureInput(existing, IN_DISPLAY, 'monitor_capture', { capture_cursor: true }, false);
    await this.ensureInput(existing, IN_GAME_AUDIO, 'wasapi_process_output_capture', { priority: 2 }, false);
    await this.ensureInput(existing, IN_DISCORD, 'wasapi_process_output_capture', { priority: 2 }, false);
    // Global audio: use OBS's special inputs if present, else create our own.
    const special = await this.call('GetSpecialInputs');
    this.desktopName = special.desktop1 || IN_DESKTOP;
    this.micName = special.mic1 || IN_MIC;
    if (!special.desktop1) await this.ensureInput(existing, IN_DESKTOP, 'wasapi_output_capture', { device_id: 'default' });
    if (!special.mic1) await this.ensureInput(existing, IN_MIC, 'wasapi_input_capture', { device_id: 'default' });
    await this.fitToCanvas(IN_GAME);
    await this.fitToCanvas(IN_DISPLAY);
    await this.applyAudio();
    await this.applyMonitor();
  }

  async listItems(inputName, propertyName) {
    try {
      const { propertyItems } = await this.call('GetInputPropertiesListPropertyItems', { inputName, propertyName });
      return propertyItems || [];
    } catch {
      return [];
    }
  }

  async getDevices() {
    if (this.status.obsState !== 'ready') return { outputs: [], inputs: [], monitors: [] };
    const [outputs, inputs, monitors] = await Promise.all([
      this.listItems(this.desktopName, 'device_id'),
      this.listItems(this.micName, 'device_id'),
      this.listItems(IN_DISPLAY, 'monitor_id')
    ]);
    const map = (arr) => arr.map((i) => ({ id: i.itemValue, name: i.itemName }));
    return { outputs: map(outputs), inputs: map(inputs), monitors: map(monitors) };
  }

  async applyMonitor() {
    const s = this.settings.get();
    const monitors = await this.listItems(IN_DISPLAY, 'monitor_id');
    const m = monitors[s.recording.monitorIndex] || monitors[0];
    if (m) await this.call('SetInputSettings', { inputName: IN_DISPLAY, inputSettings: { monitor_id: m.itemValue, monitor: s.recording.monitorIndex } }).catch(() => {});
  }

  async applyAudio(profile) {
    const a = this.settings.get().audio;
    const micEnabled = profile && profile.micEnabled !== undefined ? profile.micEnabled : a.micEnabled;
    const safe = (p) => p.catch(() => {});
    // Desktop audio (track 1 mix + track 2)
    await safe(this.call('SetInputSettings', { inputName: this.desktopName, inputSettings: { device_id: a.desktopDevice || 'default' } }));
    await safe(this.call('SetInputVolume', { inputName: this.desktopName, inputVolumeMul: a.desktopVolume }));
    await safe(this.call('SetInputMute', { inputName: this.desktopName, inputMuted: a.gameAudioOnly }));
    await safe(this.call('SetInputAudioTracks', { inputName: this.desktopName, inputAudioTracks: tracks([1, 2]) }));
    // Game-process audio replaces desktop audio when gameAudioOnly is on
    await safe(this.call('SetInputAudioTracks', { inputName: IN_GAME_AUDIO, inputAudioTracks: tracks([1, 2]) }));
    await safe(this.call('SetInputVolume', { inputName: IN_GAME_AUDIO, inputVolumeMul: a.desktopVolume }));
    await this.setItemEnabled(IN_GAME_AUDIO, a.gameAudioOnly && !!this.status.game);
    // Microphone (track 1 mix + track 3)
    await safe(this.call('SetInputSettings', { inputName: this.micName, inputSettings: { device_id: a.micDevice || 'default' } }));
    await safe(this.call('SetInputVolume', { inputName: this.micName, inputVolumeMul: a.micVolume }));
    await safe(this.call('SetInputAudioTracks', { inputName: this.micName, inputAudioTracks: tracks([1, 3]) }));
    await safe(this.call('SetInputMute', { inputName: this.micName, inputMuted: !micEnabled }));
    this.setStatus({ micMuted: !micEnabled });
    // Noise suppression (RNNoise)
    const filters = await this.call('GetSourceFilterList', { sourceName: this.micName }).catch(() => ({ filters: [] }));
    if (!filters.filters.find((f) => f.filterName === NOISE_FILTER)) {
      await safe(this.call('CreateSourceFilter', {
        sourceName: this.micName, filterName: NOISE_FILTER, filterKind: 'noise_suppress_filter_v2', filterSettings: { method: 'rnnoise' }
      }));
    }
    await safe(this.call('SetSourceFilterEnabled', { sourceName: this.micName, filterName: NOISE_FILTER, filterEnabled: !!a.noiseSuppression }));
    // Discord on its own track 4 (desktop audio already includes it in the mix,
    // unless desktop is muted because we capture game audio only).
    await safe(this.call('SetInputAudioTracks', { inputName: IN_DISCORD, inputAudioTracks: tracks(a.gameAudioOnly ? [1, 4] : [4]) }));
    if (a.separateDiscordTrack) {
      const items = await this.listItems(IN_DISCORD, 'window');
      const d = items.find((i) => /:discord\.exe$/i.test(i.itemValue));
      if (d) await safe(this.call('SetInputSettings', { inputName: IN_DISCORD, inputSettings: { window: d.itemValue, priority: 2 } }));
      await this.setItemEnabled(IN_DISCORD, !!d);
    } else {
      await this.setItemEnabled(IN_DISCORD, false);
    }
  }

  // ---------- capture / game switching ----------
  async findWindowFor(exe) {
    const items = await this.listItems(IN_GAME, 'window');
    const lower = exe.toLowerCase();
    return items.find((i) => (i.itemValue || '').toLowerCase().endsWith(':' + lower));
  }

  async captureGameWindow(game) {
    clearInterval(this.windowRetry);
    const apply = async () => {
      const win = await this.findWindowFor(game.exe);
      if (!win) return false;
      await this.call('SetInputSettings', {
        inputName: IN_GAME,
        inputSettings: { capture_mode: 'window', window: win.itemValue, priority: 2 }
      }).catch(() => {});
      const audioWin = (await this.listItems(IN_GAME_AUDIO, 'window')).find((i) => i.itemValue.toLowerCase().endsWith(':' + game.exe.toLowerCase()));
      if (audioWin) await this.call('SetInputSettings', { inputName: IN_GAME_AUDIO, inputSettings: { window: audioWin.itemValue, priority: 2 } }).catch(() => {});
      this.setStatus({ capturingWindow: win.itemName });
      return true;
    };
    // Start with "any fullscreen" so nothing is missed while the window spawns.
    await this.call('SetInputSettings', { inputName: IN_GAME, inputSettings: { capture_mode: 'any_fullscreen' } }).catch(() => {});
    if (await apply()) return;
    let tries = 0;
    this.windowRetry = setInterval(async () => {
      tries++;
      if (tries > 40 || !this.status.game || this.status.game.exe !== game.exe) return clearInterval(this.windowRetry);
      if (await apply()) clearInterval(this.windowRetry);
    }, 3000);
  }

  async stopOutputsForReconfig() {
    if (this.status.replayActive) await this.stopReplay();
  }

  profileFor(game) {
    if (!game) return null;
    const p = this.settings.get().games.profiles[game.id];
    return p && p.enabled ? p : null;
  }

  /** Called by the game detector. game = {id,name,exe} or null */
  /** Serialised: detector events and settings changes can arrive together. */
  setGame(game) {
    this.lastGame = game;
    const run = () => this._setGame(game);
    this.gameQueue = (this.gameQueue || Promise.resolve()).then(run, run);
    return this.gameQueue;
  }

  async _setGame(game) {
    if (this.status.obsState !== 'ready') {
      this.setStatus({ game });
      return;
    }
    const s = this.settings.get();
    const prev = this.status.game;
    this.setStatus({ game, capturingWindow: null });
    const profile = this.profileFor(game);
    const desktopMode = s.recording.captureMode === 'desktop';

    if (prev && (!game || prev.exe !== game.exe) && this.autoStartedRecording && this.status.recording) {
      await this.call('StopRecord').catch(() => {});
      this.autoStartedRecording = false;
    }

    const wantReplay = s.recording.replayEnabled && (!!game || desktopMode || s.recording.recordDesktopWhenNoGame);
    const useDisplay = desktopMode || !game;

    try {
      await this.reconfigure(profile);
    } catch (e) {
      this.emit('log', `reconfigure failed: ${e.message}`);
    }
    await this.setItemEnabled(IN_GAME, !useDisplay);
    await this.setItemEnabled(IN_DISPLAY, useDisplay);
    this.setStatus({ captureMode: useDisplay ? 'desktop' : 'game' });
    if (game && !desktopMode) await this.captureGameWindow(game);
    await this.applyAudio(profile);

    if (wantReplay && !this.status.replayActive) await this.startReplay();
    if (!wantReplay && this.status.replayActive) await this.stopReplay();

    if (game && s.recording.autoRecordSessions && !this.status.recording && (!prev || prev.exe !== game.exe)) {
      await this.call('StartRecord').then(() => { this.autoStartedRecording = true; }).catch(() => {});
    }
  }

  async startReplay() {
    let lastErr = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        await this.call('StartReplayBuffer');
        const ok = await this.waitFor(() => this.replayState === 'OBS_WEBSOCKET_OUTPUT_STARTED' || this.status.replayActive, 6000);
        if (!ok) throw new Error('Replay buffer did not start');
        this.encoderVerified = true;
        if (this.status.error) this.setStatus({ error: null });
        return;
      } catch (e) {
        if (/already active|running/i.test(e.message || '') || this.status.replayActive) return;
        lastErr = e;
        // the previous output may still be shutting down - give it a moment
        await sleep(800 + attempt * 700);
      }
    }
    const msg = (lastErr && (lastErr.message || `error code ${lastErr.code}`)) || 'unknown error';
    // Hardware encoder failed on first use (e.g. outdated driver) → fall back to x264 once.
    if (!this.encoderVerified && this.settings.get().quality.encoder === 'auto' && !this.forceX264 && this.status.vendor !== 'x264') {
      this.forceX264 = true;
      this.setStatus({ error: `Hardware encoder failed (${msg}); falling back to x264.` });
      setTimeout(() => this.restart(), 0);
      return;
    }
    this.setStatus({ error: `Replay buffer failed to start: ${msg}` });
  }

  async stopReplay() {
    await this.call('StopReplayBuffer').catch(() => {});
    await this.waitFor(() => this.replayState === 'OBS_WEBSOCKET_OUTPUT_STOPPED' || !this.status.replayActive, 8000);
    await sleep(300);
  }

  async toggleReplay() {
    if (this.status.replayActive) await this.stopReplay();
    else await this.startReplay();
  }

  /**
   * Save the replay buffer. opts: { seconds, source, label, event, delayMs }
   * Resolves with the raw saved path (post-processing happens in the clip pipeline).
   */
  async saveClip(opts = {}) {
    if (this.status.obsState !== 'ready') throw new Error('Recorder is not ready');
    if (!this.status.replayActive) throw new Error('Replay buffer is not running');
    if (opts.delayMs) await sleep(opts.delayMs);
    return new Promise((resolve, reject) => {
      const entry = { opts, resolve, reject };
      entry.timer = setTimeout(async () => {
        // Older obs-websocket versions may not emit ReplayBufferSaved.
        const idx = this.pendingSaves.indexOf(entry);
        if (idx === -1) return;
        try {
          const { savedReplayPath } = await this.call('GetLastReplayBufferReplay');
          this.onReplaySaved(savedReplayPath);
        } catch (e) {
          this.pendingSaves.splice(idx, 1);
          reject(new Error('Timed out waiting for replay to save'));
        }
      }, 20000);
      this.pendingSaves.push(entry);
      this.call('SaveReplayBuffer').catch((e) => {
        const idx = this.pendingSaves.indexOf(entry);
        if (idx !== -1) this.pendingSaves.splice(idx, 1);
        clearTimeout(entry.timer);
        reject(e);
      });
    });
  }

  async startRecording() {
    await this.call('StartRecord');
    this.autoStartedRecording = false;
  }
  async stopRecording() {
    await this.call('StopRecord');
  }
  async toggleRecording() {
    if (this.status.recording) return this.stopRecording();
    return this.startRecording();
  }

  addBookmark(label = 'Bookmark') {
    if (!this.status.recording || !this.status.recordStartedAt) return null;
    const t = (Date.now() - this.status.recordStartedAt) / 1000;
    const bm = { time: Math.max(0, t - 2), label };
    this.bookmarks.push(bm);
    this.call('CreateRecordChapter', { chapterName: label }).catch(() => {});
    return bm;
  }

  async screenshot(file) {
    const v = await this.call('GetVideoSettings');
    await this.call('SaveSourceScreenshot', {
      sourceName: SCENE, imageFormat: 'png', imageFilePath: file,
      imageWidth: v.outputWidth, imageHeight: v.outputHeight
    });
    return file;
  }

  async toggleMic() {
    const muted = !this.status.micMuted;
    await this.call('SetInputMute', { inputName: this.micName, inputMuted: muted }).catch(() => {});
    this.setStatus({ micMuted: muted });
    return muted;
  }

  /** Apply changed settings. Only a different OBS install needs a restart. */
  async onSettingsChanged(patch) {
    if (this.status.obsState !== 'ready') return;
    if (patch.advanced && patch.advanced.obsPath !== undefined) {
      await this.restart();
      return;
    }
    if (patch.quality && patch.quality.encoder !== undefined) this.forceX264 = false;
    if (patch.performance && patch.performance.obsPriority) this.applyPriority();
    if (patch.audio) await this.applyAudio(this.profileFor(this.status.game));
    if (patch.recording && patch.recording.monitorIndex !== undefined) await this.applyMonitor();
    // setGame() re-plans, hot-swaps the OBS profile if anything changed,
    // and restarts the replay buffer as needed.
    await this.setGame(this.status.game);
  }
}

module.exports = { ObsManager, SCENE };
