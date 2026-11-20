// Click-through, always-on-top notification overlay (works over borderless /
// windowed games; exclusive-fullscreen games only get the capture sound).
const { BrowserWindow, screen } = require('electron');
const path = require('path');

class Overlay {
  constructor(settings) {
    this.settings = settings;
    this.win = null;
  }

  ensure() {
    if (this.win && !this.win.isDestroyed()) return this.win;
    const d = screen.getPrimaryDisplay().workArea;
    const w = 380;
    const h = 120;
    const pos = this.settings.get().overlay.position;
    const x = pos.includes('left') ? d.x + 24 : d.x + d.width - w - 24;
    const y = pos.includes('bottom') ? d.y + d.height - h - 24 : d.y + 24;
    this.win = new BrowserWindow({
      x, y, width: w, height: h,
      frame: false, transparent: true, resizable: false, movable: false,
      focusable: false, skipTaskbar: true, alwaysOnTop: true, show: false,
      hasShadow: false,
      webPreferences: { preload: path.join(__dirname, 'preload.js'), backgroundThrottling: false }
    });
    this.win.setAlwaysOnTop(true, 'screen-saver');
    this.win.setIgnoreMouseEvents(true);
    this.win.loadFile(path.join(__dirname, 'overlay.html'));
    this.ready = new Promise((r) => this.win.webContents.once('did-finish-load', r));
    this.win.on('closed', () => { this.win = null; });
    return this.win;
  }

  async toast(t) {
    const o = this.settings.get().overlay;
    if (!o.enabled && !o.sound) return;
    const win = this.ensure();
    await this.ready;
    win.webContents.send('overlay:toast', { ...t, showVisual: o.enabled, sound: o.sound && t.sound !== null ? t.sound || 'clip' : null, volume: o.soundVolume });
    if (o.enabled) {
      win.showInactive();
      clearTimeout(this.hideTimer);
      this.hideTimer = setTimeout(() => win && !win.isDestroyed() && win.hide(), 3200);
    }
    // free the overlay renderer when it hasn't been used for a while
    clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => { if (this.win && !this.win.isDestroyed()) this.win.destroy(); this.win = null; }, 120000);
  }

  reposition() {
    if (this.win && !this.win.isDestroyed()) { this.win.destroy(); this.win = null; }
  }
}

module.exports = { Overlay };
