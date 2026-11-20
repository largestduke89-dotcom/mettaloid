// Global hotkeys (work while games are focused).
const { globalShortcut } = require('electron');

class Hotkeys {
  constructor(settings, actions) {
    this.settings = settings;
    this.actions = actions; // { clip(seconds,label), toggleRecording, bookmark, screenshot, toggleMic, toggleReplay }
    this.failed = [];
    this.paused = false;
  }

  register() {
    globalShortcut.unregisterAll();
    this.failed = [];
    if (this.paused) return this.failed;
    const h = this.settings.get().hotkeys;
    const bind = (acc, fn, name) => {
      if (!acc) return;
      try {
        const ok = globalShortcut.register(acc, fn);
        if (!ok) this.failed.push({ accelerator: acc, name });
      } catch {
        this.failed.push({ accelerator: acc, name, invalid: true });
      }
    };
    for (const c of h.clips || []) bind(c.accelerator, () => this.actions.clip(Number(c.seconds), c.label), c.label || 'Clip');
    bind(h.toggleRecording, () => this.actions.toggleRecording(), 'Start/stop recording');
    bind(h.bookmark, () => this.actions.bookmark(), 'Bookmark');
    bind(h.screenshot, () => this.actions.screenshot(), 'Screenshot');
    bind(h.toggleMic, () => this.actions.toggleMic(), 'Toggle microphone');
    bind(h.toggleReplay, () => this.actions.toggleReplay(), 'Toggle replay buffer');
    return this.failed;
  }

  // Used while the user is capturing a new hotkey in Settings.
  pause() {
    this.paused = true;
    globalShortcut.unregisterAll();
  }
  resume() {
    this.paused = false;
    return this.register();
  }

  dispose() {
    globalShortcut.unregisterAll();
  }
}

module.exports = { Hotkeys };
