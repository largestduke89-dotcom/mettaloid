// Polls running processes and reports the active game (built-in DB + user games).
const { execFile } = require('child_process');
const EventEmitter = require('events');
const builtin = require('./db.json');

function tasklist(args) {
  return new Promise((resolve) => {
    execFile('tasklist', args, { windowsHide: true, maxBuffer: 16 * 1024 * 1024 }, (err, out) => resolve(err ? '' : String(out)));
  });
}

function parseCsv(out) {
  return out.split(/\r?\n/).filter(Boolean).map((line) => {
    const cols = [];
    const re = /"((?:[^"]|"")*)"/g;
    let m;
    while ((m = re.exec(line))) cols.push(m[1].replace(/""/g, '"'));
    return cols;
  });
}

class GameDetector extends EventEmitter {
  constructor(settings) {
    super();
    this.settings = settings;
    this.current = null;
    this.missCount = 0;
    this.timer = null;
  }

  games() {
    const s = this.settings.get().games;
    const custom = (s.custom || []).map((g) => ({ ...g, exe: Array.isArray(g.exe) ? g.exe : [g.exe], custom: true, category: g.category || 'Custom' }));
    const disabled = new Set(s.disabled || []);
    return [...custom, ...builtin].filter((g) => !disabled.has(g.id));
  }

  // Cheap scheduling: while a game is running we only check that its PID is
  // still alive (no process spawn); full process scans happen every
  // `detectionInterval` seconds when idle and every 10 s while gaming.
  start() {
    this.stop();
    const tick = async () => {
      const idleMs = Math.max(1, Number(this.settings.get().performance?.detectionInterval) || 3) * 1000;
      if (this.current) {
        let alive = true;
        try { process.kill(this.current.pid, 0); } catch (e) { alive = e.code === 'EPERM'; }
        if (!alive || Date.now() - (this.lastFull || 0) > 10000) await this.poll();
        this.timer = setTimeout(tick, 2000);
      } else {
        await this.poll();
        this.timer = setTimeout(tick, idleMs);
      }
    };
    tick();
  }

  stop() {
    clearTimeout(this.timer);
  }

  async poll() {
    if (this.polling) return;
    this.polling = true;
    this.lastFull = Date.now();
    try {
      const rows = parseCsv(await tasklist(['/fo', 'csv', '/nh']));
      const running = new Map(rows.map((r) => [String(r[0] || '').toLowerCase(), Number(r[1])]));
      let found = null;
      for (const g of this.games()) {
        const exe = g.exe.find((e) => running.has(e.toLowerCase()));
        if (!exe) continue;
        if (g.requireWindowTitle) {
          const v = parseCsv(await tasklist(['/v', '/fo', 'csv', '/nh', '/fi', `IMAGENAME eq ${exe}`]));
          if (!v.some((r) => (r[8] || '').toLowerCase().includes(g.requireWindowTitle.toLowerCase()))) continue;
        }
        found = { id: g.id, name: g.name, exe, pid: running.get(exe.toLowerCase()), category: g.category };
        break;
      }
      if (found) {
        this.missCount = 0;
        if (!this.current || this.current.id !== found.id || this.current.pid !== found.pid) {
          this.current = found;
          this.emit('change', found);
        }
      } else if (this.current) {
        // tolerate brief gaps (launcher → game handoff)
        if (++this.missCount >= 2) {
          this.current = null;
          this.missCount = 0;
          this.emit('change', null);
        }
      }
    } finally {
      this.polling = false;
    }
  }
}

module.exports = { GameDetector, builtinGames: builtin };
