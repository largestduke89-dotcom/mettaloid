// Find Frame service: capture a reference box from a paused clip, run the
// OpenCV engine over one clip or the whole library, keep saved "finders",
// persist their results, and apply actions (tags / bookmarks) to matches.
const { spawn } = require('child_process');
const crypto = require('crypto');
const EventEmitter = require('events');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { FFMPEG, run } = require('./media/ffmpeg');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class FindFrame extends EventEmitter {
  /**
   * @param {object} o
   * @param {import('./settings').Settings} o.settings
   * @param {import('./library').Library} o.library
   * @param {string} o.userData
   * @param {string} o.appRoot
   * @param {boolean} o.isPackaged
   * @param {() => boolean} o.isGaming
   */
  constructor(o) {
    super();
    this.o = o;
    this.settings = o.settings;
    this.library = o.library;
    this.dir = path.join(o.userData, 'findframe');
    this.refDir = path.join(this.dir, 'refs');
    this.thumbDir = path.join(this.dir, 'thumbs');
    this.tmpDir = path.join(this.dir, 'jobs');
    for (const d of [this.dir, this.refDir, this.thumbDir, this.tmpDir]) fs.mkdirSync(d, { recursive: true });
    this.file = path.join(this.dir, 'finders.json');
    this.db = { finders: [], results: {} };
    try { this.db = { finders: [], results: {}, ...JSON.parse(fs.readFileSync(this.file, 'utf8')) }; } catch {}
    this.jobs = new Map();
    this.thumbQueue = Promise.resolve();
    this.autoTimer = null;
    this.autoPending = new Set();
    this.library.on('added', (clip) => this.onClipAdded(clip));
    this.cleanup();
  }

  get cfg() {
    return this.settings.get().findFrame;
  }

  // ---------------------------------------------------------------- storage
  save() {
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.flush(), 400);
  }
  flush() {
    clearTimeout(this.saveTimer);
    const tmp = this.file + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(this.db));
    fs.renameSync(tmp, this.file);
  }
  changed() {
    this.save();
    this.emit('changed');
  }

  /** Remove reference images from unsaved searches older than a day. */
  cleanup() {
    const keep = new Set(this.db.finders.flatMap((f) => [f.image, f.frameImage]).filter(Boolean).map((p) => path.basename(p)));
    for (const f of fs.readdirSync(this.refDir)) {
      const p = path.join(this.refDir, f);
      try { if (!keep.has(f) && Date.now() - fs.statSync(p).mtimeMs > 864e5) fs.rmSync(p); } catch {}
    }
    for (const f of fs.readdirSync(this.tmpDir)) fs.rmSync(path.join(this.tmpDir, f), { force: true, recursive: true });
  }

  // ---------------------------------------------------------------- engine
  engine() {
    const { isPackaged, appRoot } = this.o;
    const exe = isPackaged
      ? path.join(process.resourcesPath, 'findframe', 'findframe-engine.exe')
      : path.join(appRoot, 'findframe-engine', 'dist', 'findframe-engine', 'findframe-engine.exe');
    if (fs.existsSync(exe)) return { cmd: exe, args: [] };
    // development fallback: run the script with the venv
    const py = path.join(appRoot, 'findframe-engine', '.venv', 'Scripts', 'python.exe');
    if (!isPackaged && fs.existsSync(py)) return { cmd: py, args: [path.join(appRoot, 'findframe-engine', 'engine.py')] };
    throw new Error('Find Frame engine not found. Run "npm run build:engine".');
  }

  engineSettings(over = {}) {
    const c = { ...this.cfg, ...over };
    return {
      method: c.method, threshold: c.threshold, templateThreshold: c.templateThreshold, nfeatures: c.nfeatures,
      ratio: c.ratio, matcher: c.matcher, verifyGeometry: c.verifyGeometry, minInliers: c.minInliers, minMatches: c.minMatches,
      autoMinKeypoints: c.autoMinKeypoints, scanFps: c.scanFps, scanWidth: c.scanWidth, hwDecode: c.hwDecode,
      boostContrast: c.boostContrast, contrast: c.contrast, equalize: c.equalize, edges: c.edges, blur: c.blur,
      searchArea: c.searchArea, nearMargin: c.nearMargin, scales: c.scales, minGapSec: c.minGapSec,
      maxResultsPerClip: c.maxResultsPerClip, stopAfterFirst: c.stopAfterFirst, threads: c.threadsPerWorker, liveScores: true
    };
  }

  /** Run the engine on a job; calls onEvent for every JSON line. */
  runEngine(job, onEvent) {
    const { cmd, args } = this.engine();
    const jobFile = path.join(this.tmpDir, `${crypto.randomUUID()}.json`);
    fs.writeFileSync(jobFile, JSON.stringify(job));
    const proc = spawn(cmd, [...args, jobFile], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    if (this.cfg.lowPriority) {
      try { os.setPriority(proc.pid, os.constants.priority.PRIORITY_BELOW_NORMAL); } catch {}
    }
    let buf = '';
    let errTail = '';
    proc.stdout.on('data', (d) => {
      buf += d.toString();
      let i;
      while ((i = buf.indexOf('\n')) !== -1) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (!line) continue;
        try { onEvent(JSON.parse(line)); } catch {}
      }
    });
    proc.stderr.on('data', (d) => { errTail = (errTail + d.toString()).slice(-2000); });
    const done = new Promise((resolve, reject) => {
      proc.on('error', reject);
      proc.on('exit', (code) => {
        fs.rm(jobFile, { force: true }, () => {});
        if (code === 0 || proc.killedByUser) resolve();
        else reject(new Error(`Find Frame engine exited with code ${code}${errTail ? ': ' + errTail.split('\n').filter(Boolean).slice(-1)[0] : ''}`));
      });
    });
    return { proc, done };
  }

  // ---------------------------------------------------------------- references
  /** Grab the exact frame at `time` (full resolution) and crop the drawn box. */
  async capture({ clipId, path: p, time, box }) {
    const clip = clipId ? this.library.get(clipId) : null;
    const file = clip ? clip.path : p;
    if (!file) throw new Error('No video');
    const id = crypto.randomUUID();
    const frameImage = path.join(this.refDir, `${id}-frame.jpg`);
    const image = path.join(this.refDir, `${id}.png`);
    const b = { x: Math.max(0, box.x), y: Math.max(0, box.y), w: Math.min(1 - Math.max(0, box.x), box.w), h: Math.min(1 - Math.max(0, box.y), box.h) };
    await run(['-ss', String(Math.max(0, time)), '-i', file, '-frames:v', '1', '-q:v', '3', frameImage]).promise;
    await run(['-i', frameImage, '-vf', `crop=trunc(iw*${b.w}):trunc(ih*${b.h}):trunc(iw*${b.x}):trunc(ih*${b.y})`, '-frames:v', '1', image]).promise;
    return { refId: id, image, frameImage, box: b, time, clipId: clip ? clip.id : null, width: clip ? clip.width : 0, height: clip ? clip.height : 0 };
  }

  async analyze(ref, over = {}) {
    const preview = path.join(this.refDir, `${path.basename(ref.image, '.png')}-kp.png`);
    let result = null;
    const { done } = this.runEngine({
      mode: 'analyze', ffmpeg: FFMPEG, preview,
      settings: this.engineSettings(over),
      finders: [{ id: 'analyze', image: ref.image, box: ref.box, frameWidth: ref.width, frameHeight: ref.height, settings: over }]
    }, (e) => { if (e.type === 'analysis') result = e; });
    await done;
    return result;
  }

  // ---------------------------------------------------------------- finders
  finders() {
    return this.db.finders.map((f) => ({ ...f, matchCount: this.countMatches(f.id), clipCount: Object.keys((this.db.results[f.id] || {}).matches || {}).length }));
  }
  finder(id) {
    return this.db.finders.find((f) => f.id === id);
  }
  createFinder(data) {
    const f = {
      id: crypto.randomUUID(),
      name: data.name || 'Finder',
      image: data.image,
      frameImage: data.frameImage,
      box: data.box,
      sourceClipId: data.clipId || null,
      sourceTime: data.time || 0,
      settings: data.settings || {},
      autoScan: data.autoScan !== undefined ? data.autoScan : true,
      tag: data.tag || '',
      color: data.color || '#f5b70a',
      createdAt: Date.now()
    };
    this.db.finders.push(f);
    this.changed();
    return f;
  }
  updateFinder(id, patch) {
    const f = this.finder(id);
    if (!f) return null;
    for (const k of ['name', 'settings', 'autoScan', 'tag', 'color']) if (patch[k] !== undefined) f[k] = patch[k];
    // changing match settings invalidates "already scanned" bookkeeping
    if (patch.settings && this.db.results[id]) this.db.results[id].scanned = {};
    this.changed();
    return f;
  }
  deleteFinder(id) {
    const f = this.finder(id);
    this.db.finders = this.db.finders.filter((x) => x.id !== id);
    delete this.db.results[id];
    if (f) for (const p of [f.image, f.frameImage]) try { fs.rmSync(p, { force: true }); } catch {}
    this.changed();
  }

  countMatches(fid) {
    const r = this.db.results[fid];
    return r ? Object.values(r.matches || {}).reduce((t, m) => t + m.length, 0) : 0;
  }
  results(fid) {
    const r = this.db.results[fid] || { matches: {} };
    const out = [];
    for (const [clipId, list] of Object.entries(r.matches || {})) {
      const clip = this.library.get(clipId);
      if (!clip) continue;
      out.push({ clipId, matches: list.slice().sort((a, b) => a.t - b.t) });
    }
    return out.sort((a, b) => (this.library.get(b.clipId).createdAt || 0) - (this.library.get(a.clipId).createdAt || 0));
  }
  clearResults(fid) {
    delete this.db.results[fid];
    this.changed();
  }
  removeMatch(fid, clipId, t) {
    const r = this.db.results[fid];
    if (!r || !r.matches[clipId]) return;
    r.matches[clipId] = r.matches[clipId].filter((m) => Math.abs(m.t - t) > 1e-3);
    if (!r.matches[clipId].length) delete r.matches[clipId];
    this.changed();
  }

  clipSignature(c) {
    return `${c.size}|${Math.round(c.duration * 10)}`;
  }

  // ---------------------------------------------------------------- searching
  /**
   * spec = {
   *   label, finderIds: [...] | null, reference: {image, box, settings, name} | null,
   *   clipIds: [...] | 'all', filters: { game, types, since }, rescan: bool
   * }
   */
  search(spec) {
    const id = crypto.randomUUID();
    const finders = [];
    if (spec.reference) {
      finders.push({ id: `tmp-${id}`, name: spec.reference.name || 'Search', image: spec.reference.image, frameImage: spec.reference.frameImage, box: spec.reference.box, settings: spec.reference.settings || {}, temp: true });
    }
    for (const fid of spec.finderIds || []) {
      const f = this.finder(fid);
      if (f) finders.push(f);
    }
    if (!finders.length) throw new Error('Nothing to search for');

    let clips = spec.clipIds === 'all' || !spec.clipIds ? this.library.list() : spec.clipIds.map((c) => this.library.get(c)).filter(Boolean);
    clips = clips.filter((c) => c.type !== 'screenshot' && c.duration > 0 && fs.existsSync(c.path));
    const fl = spec.filters || {};
    if (fl.game) clips = clips.filter((c) => (c.game ? c.game.name : 'Desktop') === fl.game);
    if (fl.types && fl.types.length) clips = clips.filter((c) => fl.types.includes(c.type));
    if (fl.since) clips = clips.filter((c) => c.createdAt >= fl.since);
    if (fl.favorites) clips = clips.filter((c) => c.favorite);

    const job = {
      id, label: spec.label || finders.map((f) => f.name).join(', '), kind: spec.reference ? 'search' : 'scan',
      finders: finders.map((f) => ({ id: f.id, name: f.name, image: f.image, temp: !!f.temp, color: f.color })),
      reference: spec.reference || null,
      total: clips.length, done: 0, skipped: 0, status: 'running', startedAt: Date.now(),
      matches: [], current: {}, errors: [], fps: 0
    };
    this.jobs.set(id, job);
    this.emitJob(job);
    this.execute(job, finders, clips, !!spec.rescan, spec.settings || {}).catch((e) => {
      job.status = job.status === 'cancelled' ? 'cancelled' : 'error';
      job.error = e.message;
      this.emitJob(job);
    });
    return this.publicJob(job);
  }

  async execute(job, finders, clips, rescan, over) {
    await new Promise((r) => setImmediate(r)); // let callers subscribe before the first update
    const c = this.cfg;
    // incremental: skip clip × saved finder pairs already scanned with identical settings
    const work = [];
    for (const clip of clips) {
      const sig = this.clipSignature(clip);
      const todo = finders.filter((f) => f.temp || rescan || !c.skipScanned || ((this.db.results[f.id] || {}).scanned || {})[clip.id] !== sig);
      if (todo.length) work.push({ clip, finders: todo, sig });
      else job.skipped++;
    }
    job.done = job.skipped;
    this.emitJob(job);
    if (!work.length) {
      job.status = 'done';
      job.finishedAt = Date.now();
      this.emitJob(job);
      return;
    }
    // group clips that need the same finder set, split across workers
    const workers = Math.max(1, Math.min(c.workers, work.length));
    const lanes = Array.from({ length: workers }, () => []);
    work.forEach((w, i) => lanes[i % workers].push(w));
    job.procs = [];
    await Promise.all(lanes.map((lane) => this.runLane(job, lane, over)));
    if (job.status === 'running') job.status = 'done';
    job.finishedAt = Date.now();
    this.flush();
    this.emitJob(job);
  }

  async runLane(job, lane, over) {
    // one engine process per distinct finder set (usually all finders → 1 process)
    const groups = new Map();
    for (const w of lane) {
      const key = w.finders.map((f) => f.id).join('|');
      if (!groups.has(key)) groups.set(key, { finders: w.finders, items: [] });
      groups.get(key).items.push(w);
    }
    for (const g of groups.values()) {
      if (job.status !== 'running') return;
      while (this.cfg.pauseWhileGaming && this.o.isGaming() && job.status === 'running') {
        job.paused = true;
        this.emitJob(job);
        await sleep(5000);
      }
      job.paused = false;
      const byVideo = new Map(g.items.map((w) => [w.clip.id, w]));
      const engineJob = {
        ffmpeg: FFMPEG,
        settings: this.engineSettings(over),
        finders: g.finders.map((f) => ({ id: f.id, image: f.image, box: f.box, settings: f.settings || {} })),
        videos: g.items.map((w) => ({ id: w.clip.id, path: w.clip.path, width: w.clip.width, height: w.clip.height, fps: w.clip.fps, duration: w.clip.duration }))
      };
      const { proc, done } = this.runEngine(engineJob, (e) => this.onEngineEvent(job, e, byVideo));
      job.procs.push(proc);
      await done;
    }
  }

  onEngineEvent(job, e, byVideo) {
    if (e.type === 'progress') {
      job.current[e.video] = { t: e.t, duration: e.duration, scores: e.scores };
      job.fps = e.fps;
      this.emitJobThrottled(job);
    } else if (e.type === 'video-start') {
      job.current[e.video] = { t: 0, duration: (this.library.get(e.video) || {}).duration || 0, info: e.finders };
      this.emitJobThrottled(job);
    } else if (e.type === 'video-done') {
      delete job.current[e.video];
      job.done++;
      const w = byVideo.get(e.video);
      if (w) {
        for (const f of w.finders) {
          if (f.temp) continue;
          const r = (this.db.results[f.id] = this.db.results[f.id] || { matches: {}, scanned: {} });
          r.scanned = r.scanned || {};
          r.scanned[e.video] = w.sig;
        }
        this.save();
      }
      this.emitJob(job);
    } else if (e.type === 'match') {
      this.onMatch(job, e);
    } else if (e.type === 'error') {
      job.errors.push(e.message);
      this.emitJob(job);
    }
  }

  onMatch(job, e) {
    const c = this.cfg;
    const m = {
      id: crypto.randomUUID(), finderId: e.finder, clipId: e.video, t: e.t, start: e.start, end: e.end,
      score: e.score, box: e.box, method: e.method, thumb: null, foundAt: Date.now()
    };
    job.matches.push(m);
    const finder = this.finder(e.finder);
    if (finder) {
      const r = (this.db.results[finder.id] = this.db.results[finder.id] || { matches: {}, scanned: {} });
      const list = (r.matches[e.video] = r.matches[e.video] || []);
      const dup = list.find((x) => Math.abs(x.t - m.t) < Math.max(1, c.minGapSec));
      if (dup) Object.assign(m, { id: dup.id });
      else list.push(m);
      this.applyActions(finder, m);
      this.save();
    }
    this.emit('match', { jobId: job.id, match: m });
    this.emitJobThrottled(job);
    if (c.thumbnails) this.queueThumb(m, finder);
  }

  applyActions(finder, m) {
    const c = this.cfg;
    const clip = this.library.get(m.clipId);
    if (!clip) return;
    const patch = {};
    if (c.autoTag) {
      const tag = (finder.tag || finder.name).trim().replace(/^#/, '');
      if (tag && !(clip.tags || []).includes(tag)) patch.tags = [...(clip.tags || []), tag];
    }
    if (c.addBookmarks) {
      const label = `Find: ${finder.name}`;
      const bms = clip.bookmarks || [];
      if (!bms.some((b) => b.label === label && Math.abs(b.time - m.t) < 1.5)) patch.bookmarks = [...bms, { time: m.t, label }].sort((a, b) => a.time - b.time);
    }
    if (Object.keys(patch).length) this.library.update(clip.id, patch);
  }

  queueThumb(m, finder) {
    this.thumbQueue = this.thumbQueue.then(async () => {
      const clip = this.library.get(m.clipId);
      if (!clip) return;
      const out = path.join(this.thumbDir, `${m.clipId}-${Math.round(m.t * 1000)}.jpg`);
      if (!fs.existsSync(out)) {
        const [x, y, w, h] = m.box;
        const color = ((finder && finder.color) || '#f5b70a').replace('#', '0x');
        try {
          await run(['-ss', String(m.t), '-i', clip.path, '-frames:v', '1', '-vf',
            `scale=480:-2,drawbox=x=iw*${x}:y=ih*${y}:w=iw*${w}:h=ih*${h}:color=${color}@0.95:t=3`, '-q:v', '4', out], { lowPriority: true, threads: 1 }).promise;
        } catch { return; }
      }
      m.thumb = out;
      const r = finder && this.db.results[finder.id];
      const stored = r && (r.matches[m.clipId] || []).find((x) => x.id === m.id);
      if (stored) { stored.thumb = out; this.save(); }
      this.emit('thumb', { matchId: m.id, thumb: out });
    }).catch(() => {});
  }

  // ---------------------------------------------------------------- jobs
  publicJob(job) {
    const { procs, ...rest } = job;
    return { ...rest, matchCount: job.matches.length, matches: job.matches.slice(-500) };
  }
  emitJob(job) {
    this.emit('job', this.publicJob(job));
  }
  emitJobThrottled(job) {
    const now = Date.now();
    if (now - (job.lastEmit || 0) < 250) return;
    job.lastEmit = now;
    this.emitJob(job);
  }
  listJobs() {
    return [...this.jobs.values()].map((j) => this.publicJob(j)).sort((a, b) => b.startedAt - a.startedAt);
  }
  cancel(id) {
    const job = this.jobs.get(id);
    if (!job || job.status !== 'running') return;
    job.status = 'cancelled';
    for (const p of job.procs || []) { p.killedByUser = true; try { p.kill(); } catch {} }
    this.emitJob(job);
  }
  clearJobs() {
    for (const [id, j] of this.jobs) if (j.status !== 'running') this.jobs.delete(id);
  }
  cancelAll() {
    for (const id of this.jobs.keys()) this.cancel(id);
  }

  // ---------------------------------------------------------------- auto scan
  onClipAdded(clip) {
    if (!this.cfg.autoScanNewClips || clip.type === 'screenshot') return;
    if (!this.db.finders.some((f) => f.autoScan)) return;
    this.autoPending.add(clip.id);
    clearTimeout(this.autoTimer);
    // batch new clips; wait a bit so post-processing has finished
    this.autoTimer = setTimeout(() => {
      const ids = [...this.autoPending];
      this.autoPending.clear();
      const finderIds = this.db.finders.filter((f) => f.autoScan).map((f) => f.id);
      if (ids.length && finderIds.length) {
        try { this.search({ label: `Auto-scan · ${ids.length} new clip${ids.length > 1 ? 's' : ''}`, finderIds, clipIds: ids }); } catch {}
      }
    }, 8000);
  }
}

module.exports = { FindFrame };
