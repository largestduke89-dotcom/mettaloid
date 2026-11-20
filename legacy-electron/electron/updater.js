// Auto-updates from GitHub Releases (electron-updater). The repo is set in
// package.json → build.publish (owner/repo). Releases are produced by
// .github/workflows/release.yml when you push a tag like v0.2.0.
const { app } = require('electron');
const EventEmitter = require('events');

class Updater extends EventEmitter {
  constructor(settings) {
    super();
    this.settings = settings;
    this.state = { status: 'idle', version: app.getVersion(), available: null, progress: 0, error: null };
    this.enabled = app.isPackaged;
    if (!this.enabled) {
      this.state.status = 'dev';
      return;
    }
    const { autoUpdater } = require('electron-updater');
    this.au = autoUpdater;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.on('checking-for-update', () => this.set({ status: 'checking', error: null }));
    autoUpdater.on('update-available', (info) => this.set({ status: 'available', available: info.version, notes: info.releaseNotes }));
    autoUpdater.on('update-not-available', () => this.set({ status: 'latest' }));
    autoUpdater.on('download-progress', (p) => this.set({ status: 'downloading', progress: p.percent / 100 }));
    autoUpdater.on('update-downloaded', (info) => this.set({ status: 'ready', available: info.version, progress: 1 }));
    autoUpdater.on('error', (e) => this.set({ status: 'error', error: String(e && e.message ? e.message : e).split('\n')[0] }));
  }

  set(p) {
    Object.assign(this.state, p);
    this.emit('status', { ...this.state });
  }

  async check() {
    if (!this.enabled) return this.state;
    this.au.autoDownload = this.settings.get().updates.autoDownload;
    this.au.allowPrerelease = this.settings.get().updates.channel === 'beta';
    try {
      await this.au.checkForUpdates();
    } catch (e) {
      this.set({ status: 'error', error: String(e.message || e).split('\n')[0] });
    }
    return this.state;
  }

  async download() {
    if (!this.enabled) return;
    await this.au.downloadUpdate();
  }

  install() {
    if (!this.enabled) return;
    setImmediate(() => this.au.quitAndInstall(false, true));
  }

  startSchedule() {
    if (!this.enabled) return;
    const tick = () => this.settings.get().updates.autoCheck && this.check();
    setTimeout(tick, 15000);
    this.timer = setInterval(tick, 4 * 60 * 60 * 1000);
  }
}

module.exports = { Updater };
