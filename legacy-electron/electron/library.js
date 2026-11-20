// Clip library: metadata DB (JSON), thumbnails, imports, watch folders,
// replay post-processing and storage cleanup.
const EventEmitter = require('events');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { probe, thumbnail, trimTail } = require('./media/ffmpeg');

const VIDEO_EXT = /\.(mp4|mkv|mov|webm|avi|flv|ts|m4v)$/i;
const IMAGE_EXT = /\.(png|jpe?g|webp|bmp)$/i;

const sanitize = (s) => String(s || 'Clip').replace(/[<>:"/\\|?*\x00-\x1f]/g, '').trim().slice(0, 80) || 'Clip';
const stamp = (d = new Date()) => {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}`;
};

function uniquePath(p) {
  if (!fs.existsSync(p)) return p;
  const ext = path.extname(p);
  const base = p.slice(0, -ext.length);
  for (let i = 2; ; i++) {
    const c = `${base} (${i})${ext}`;
    if (!fs.existsSync(c)) return c;
  }
}

// Limit concurrent probe/thumbnail work (e.g. importing hundreds of files).
let active = 0;
const waiters = [];
async function limited(fn) {
  if (active >= 2) await new Promise((r) => waiters.push(r));
  active++;
  try { return await fn(); } finally { active--; const n = waiters.shift(); if (n) n(); }
}

async function moveFile(src, dst) {
  try {
    await fs.promises.rename(src, dst);
  } catch (e) {
    if (e.code !== 'EXDEV') throw e;
    await fs.promises.copyFile(src, dst);
    await fs.promises.unlink(src);
  }
}

// OBS may still hold the file briefly after the "saved" event.
async function waitUnlocked(file, timeout = 10000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try {
      const fd = fs.openSync(file, 'r+');
      fs.closeSync(fd);
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 250));
    }
  }
}

class Library extends EventEmitter {
  constructor({ settings, userData }) {
    super();
    this.settings = settings;
    this.file = path.join(userData, 'library.json');
    this.thumbDir = path.join(userData, 'thumbs');
    fs.mkdirSync(this.thumbDir, { recursive: true });
    this.db = { clips: [], albums: [] };
    try {
      this.db = { clips: [], albums: [], ...JSON.parse(fs.readFileSync(this.file, 'utf8')) };
    } catch {}
    this.watchers = new Map();
    this.saveTimer = null;
  }

  // ----- persistence -----
  save() {
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.flush(), 300);
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

  get clipFolder() {
    return this.settings.get().general.clipFolder;
  }

  list() {
    return this.db.clips.slice().sort((a, b) => b.createdAt - a.createdAt);
  }
  get(id) {
    return this.db.clips.find((c) => c.id === id);
  }
  albums() {
    return this.db.albums;
  }

  // ----- add / import -----
  async add(file, meta = {}) {
    const existing = this.db.clips.find((c) => path.resolve(c.path).toLowerCase() === path.resolve(file).toLowerCase());
    if (existing) return existing;
    const id = crypto.randomUUID();
    const isImage = IMAGE_EXT.test(file);
    let info = { duration: 0, width: 0, height: 0, fps: 0, audioTracks: [], size: fs.statSync(file).size, videoCodec: null };
    const thumb = path.join(this.thumbDir, `${id}.jpg`);
    await limited(async () => {
      try { info = await probe(file); } catch {}
      try {
        await thumbnail(file, thumb, isImage ? 0 : Math.min(info.duration / 2, Math.max(0, info.duration - 1)));
      } catch {}
    });
    const clip = {
      id,
      type: isImage ? 'screenshot' : 'clip',
      path: file,
      title: path.basename(file, path.extname(file)),
      game: null,
      createdAt: Date.now(),
      duration: info.duration,
      size: info.size || fs.statSync(file).size,
      width: info.width,
      height: info.height,
      fps: info.fps,
      videoCodec: info.videoCodec,
      audioTracks: info.audioTracks,
      thumb: fs.existsSync(thumb) ? thumb : null,
      favorite: false,
      tags: [],
      albums: [],
      bookmarks: [],
      external: !path.resolve(file).toLowerCase().startsWith(path.resolve(this.clipFolder).toLowerCase()),
      ...meta
    };
    this.db.clips.push(clip);
    this.changed();
    this.emit('added', clip);
    this.enforceStorage().catch(() => {});
    return clip;
  }

  async importFiles(files, { copy = false } = {}) {
    const out = [];
    for (const f of files) {
      if (!VIDEO_EXT.test(f) && !IMAGE_EXT.test(f)) continue;
      let target = f;
      if (copy) {
        const dir = path.join(this.clipFolder, 'Imported');
        fs.mkdirSync(dir, { recursive: true });
        target = uniquePath(path.join(dir, path.basename(f)));
        await fs.promises.copyFile(f, target);
      }
      out.push(await this.add(target, { type: IMAGE_EXT.test(f) ? 'screenshot' : 'import', createdAt: fs.statSync(f).mtimeMs }));
    }
    return out;
  }

  gameDir(game) {
    const dir = path.join(this.clipFolder, sanitize(game ? game.name : 'Desktop'));
    fs.mkdirSync(dir, { recursive: true });
    return dir;
  }

  /** Post-process a replay saved by OBS: trim to requested length, rename, index. */
  async ingestReplay({ path: raw, seconds, bufferSeconds, game, source, event, label }) {
    await waitUnlocked(raw);
    const ext = path.extname(raw);
    const name = `${sanitize(game ? game.name : 'Desktop')} ${stamp()}${ext}`;
    const final = uniquePath(path.join(this.gameDir(game), name));
    if (seconds && bufferSeconds && seconds < bufferSeconds - 0.5) {
      try {
        await trimTail(raw, final, seconds);
        await fs.promises.unlink(raw).catch(() => {});
      } catch {
        await moveFile(raw, final);
      }
    } else {
      await moveFile(raw, final);
    }
    return this.add(final, {
      type: 'clip',
      game: game ? { id: game.id, name: game.name } : null,
      source: source || 'hotkey',
      event: event || null,
      title: label && label !== 'Save clip' ? `${label}${game ? ' – ' + game.name : ''}` : `${game ? game.name : 'Desktop'} clip`
    });
  }

  async ingestRecording({ path: raw, bookmarks, game, startedAt }) {
    await waitUnlocked(raw, 20000);
    const ext = path.extname(raw);
    const dir = path.join(this.gameDir(game), 'Sessions');
    fs.mkdirSync(dir, { recursive: true });
    const final = uniquePath(path.join(dir, `${sanitize(game ? game.name : 'Desktop')} session ${stamp(new Date(startedAt))}${ext}`));
    await moveFile(raw, final);
    return this.add(final, {
      type: 'session',
      game: game ? { id: game.id, name: game.name } : null,
      bookmarks: bookmarks || [],
      createdAt: startedAt,
      title: `${game ? game.name : 'Desktop'} session`
    });
  }

  async addScreenshot(file, game) {
    return this.add(file, { type: 'screenshot', game: game ? { id: game.id, name: game.name } : null, title: `${game ? game.name : 'Desktop'} screenshot` });
  }

  // ----- edit -----
  update(id, patch) {
    const c = this.get(id);
    if (!c) return null;
    const allowed = ['title', 'favorite', 'tags', 'albums', 'bookmarks', 'game', 'notes', 'locked'];
    for (const k of allowed) if (patch[k] !== undefined) c[k] = patch[k];
    if (patch.title && patch.renameFile) {
      const target = uniquePath(path.join(path.dirname(c.path), sanitize(patch.title) + path.extname(c.path)));
      try {
        fs.renameSync(c.path, target);
        c.path = target;
      } catch {}
    }
    this.changed();
    return c;
  }

  async remove(ids, { deleteFiles = true } = {}) {
    const { shell } = require('electron');
    const removed = [];
    for (const id of ids) {
      const c = this.get(id);
      if (!c) continue;
      if (deleteFiles && !c.external && fs.existsSync(c.path)) {
        // Move to Recycle Bin rather than permanently deleting.
        try { await shell.trashItem(c.path); } catch { try { fs.unlinkSync(c.path); } catch {} }
      }
      if (c.thumb) try { fs.unlinkSync(c.thumb); } catch {}
      this.db.clips = this.db.clips.filter((x) => x.id !== id);
      removed.push(id);
    }
    this.changed();
    return removed;
  }

  // ----- albums -----
  createAlbum(name) {
    const a = { id: crypto.randomUUID(), name: name || 'New album', createdAt: Date.now() };
    this.db.albums.push(a);
    this.changed();
    return a;
  }
  renameAlbum(id, name) {
    const a = this.db.albums.find((x) => x.id === id);
    if (a) { a.name = name; this.changed(); }
    return a;
  }
  deleteAlbum(id) {
    this.db.albums = this.db.albums.filter((a) => a.id !== id);
    for (const c of this.db.clips) c.albums = (c.albums || []).filter((x) => x !== id);
    this.changed();
  }
  addToAlbum(albumId, ids) {
    for (const id of ids) {
      const c = this.get(id);
      if (c && !c.albums.includes(albumId)) c.albums.push(albumId);
    }
    this.changed();
  }
  removeFromAlbum(albumId, ids) {
    for (const id of ids) {
      const c = this.get(id);
      if (c) c.albums = c.albums.filter((x) => x !== albumId);
    }
    this.changed();
  }

  // ----- maintenance -----
  /** Drop entries whose file vanished; index untracked files in the clip folder. */
  async scan() {
    let dirty = false;
    for (const c of this.db.clips.slice()) {
      if (!fs.existsSync(c.path)) {
        this.db.clips = this.db.clips.filter((x) => x !== c);
        dirty = true;
      }
    }
    const known = new Set(this.db.clips.map((c) => path.resolve(c.path).toLowerCase()));
    const walk = (dir, depth = 0) => {
      let out = [];
      if (depth > 3) return out;
      let entries = [];
      try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return out; }
      for (const e of entries) {
        if (e.name.startsWith('.')) continue;
        const p = path.join(dir, e.name);
        if (e.isDirectory()) out = out.concat(walk(p, depth + 1));
        else if (VIDEO_EXT.test(e.name) || IMAGE_EXT.test(e.name)) out.push(p);
      }
      return out;
    };
    for (const f of walk(this.clipFolder)) {
      if (known.has(path.resolve(f).toLowerCase())) continue;
      const parent = path.basename(path.dirname(f));
      await this.add(f, {
        type: IMAGE_EXT.test(f) ? 'screenshot' : /session/i.test(f) ? 'session' : 'clip',
        createdAt: fs.statSync(f).mtimeMs,
        game: parent && path.resolve(path.dirname(f)) !== path.resolve(this.clipFolder) && parent !== 'Sessions' ? { id: parent.toLowerCase(), name: parent } : null
      });
      dirty = true;
    }
    if (dirty) this.changed();
  }

  async storageStats() {
    const ours = this.db.clips.filter((c) => !c.external);
    const used = ours.reduce((t, c) => t + (c.size || 0), 0);
    let free = null;
    let total = null;
    try {
      const st = await fs.promises.statfs(this.clipFolder);
      free = st.bavail * st.bsize;
      total = st.blocks * st.bsize;
    } catch {}
    return { used, free, total, count: this.db.clips.length, folder: this.clipFolder };
  }

  /** Delete oldest unprotected clips when over the storage cap (opt-in). */
  async enforceStorage() {
    const s = this.settings.get().storage;
    if (!s.autoCleanup) return [];
    const cap = s.maxGB * 1e9;
    const ours = this.db.clips.filter((c) => !c.external);
    let used = ours.reduce((t, c) => t + (c.size || 0), 0);
    if (used <= cap) return [];
    const candidates = ours
      .filter((c) => !(s.keepFavorites && c.favorite) && !c.locked && !(c.albums && c.albums.length))
      .sort((a, b) => a.createdAt - b.createdAt);
    const del = [];
    for (const c of candidates) {
      if (used <= cap) break;
      used -= c.size || 0;
      del.push(c.id);
    }
    if (del.length) {
      await this.remove(del);
      this.emit('cleanup', del.length);
    }
    return del;
  }

  // ----- watch folders (e.g. OBS / ShadowPlay output) -----
  setWatchFolders(folders) {
    for (const [dir, w] of this.watchers) {
      if (!folders.includes(dir)) { w.close(); this.watchers.delete(dir); }
    }
    for (const dir of folders) {
      if (this.watchers.has(dir) || !fs.existsSync(dir)) continue;
      const pending = new Map();
      try {
        const w = fs.watch(dir, { recursive: true }, (_evt, name) => {
          if (!name || !(VIDEO_EXT.test(name) || IMAGE_EXT.test(name))) return;
          const full = path.join(dir, name);
          clearTimeout(pending.get(full));
          // wait until the file stops growing
          pending.set(full, setTimeout(() => this.waitStable(full).then((ok) => ok && this.add(full, { type: 'import' })), 3000));
        });
        this.watchers.set(dir, w);
      } catch {}
    }
  }

  async waitStable(file) {
    let last = -1;
    for (let i = 0; i < 120; i++) {
      let size;
      try { size = fs.statSync(file).size; } catch { return false; }
      if (size === last && size > 0) return true;
      last = size;
      await new Promise((r) => setTimeout(r, 2000));
    }
    return false;
  }
}

module.exports = { Library, sanitize, stamp, uniquePath };
