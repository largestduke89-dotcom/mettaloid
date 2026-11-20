import React, { useEffect, useState } from 'react';
import { useApp } from '../App';
import { invoke, on, useSettings, fmtSecsLabel, fmtSize } from '../api';
import { Icon, Row, Section, Select, Seg, Slider, Toggle, HotkeyInput, Bar } from '../components/ui';
import { BufferControl } from './Home';
import PlanCard, { usePlan, PerfStats } from '../components/PlanCard';

const SECTIONS = [
  ['recording', 'Recording'], ['performance', 'Performance & memory'], ['hotkeys', 'Hotkeys'], ['quality', 'Quality'], ['audio', 'Audio'],
  ['autoclip', 'Auto clipping'], ['overlay', 'Overlay & sounds'], ['storage', 'Storage'],
  ['compressor', 'Compressor'], ['editor', 'Editor'], ['updates', 'Updates'], ['windows', 'Windows'], ['advanced', 'Advanced']
];

export default function Settings({ section }) {
  const { status, toast } = useApp();
  const [s, update] = useSettings();
  const [devices, setDevices] = useState({ outputs: [], inputs: [], monitors: [] });
  const [stats, setStats] = useState(null);
  const [upd, setUpd] = useState(null);
  const [info, setInfo] = useState(null);
  const [hw, setHw] = useState(null);

  useEffect(() => {
    invoke('recorder:devices').then(setDevices).catch(() => {});
    invoke('library:stats').then(setStats);
    invoke('updater:status').then(setUpd);
    invoke('app:info').then(setInfo);
    return on('updater:status', setUpd);
  }, [status && status.obsState]);
  useEffect(() => {
    if (section) setTimeout(() => document.getElementById(section)?.scrollIntoView({ behavior: 'smooth' }), 50);
  }, [section]);

  if (!s) return null;
  const set = (group, key) => (v) => update({ [group]: { [key]: v } });

  return (
    <>
      <div className="topbar"><h1>Settings</h1></div>
      <div className="content flush" style={{ display: 'grid', gridTemplateColumns: '190px 1fr', overflow: 'hidden' }}>
        <div className="lib-side">
          {SECTIONS.map(([id, label]) => (
            <button key={id} className="nav-item" onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })}>{label}</button>
          ))}
        </div>
        <div style={{ overflow: 'auto', padding: '22px 28px 80px' }}>
          <div style={{ maxWidth: 860 }}>

            <Section title="Recording" id="recording">
              <div style={{ padding: '12px 16px' }}><BufferControl settings={s} update={update} compact /></div>
              <Row label="Replay buffer" hint="Keep the last few seconds/minutes of gameplay in memory so you can clip things after they happen.">
                <Toggle checked={s.recording.replayEnabled} onChange={set('recording', 'replayEnabled')} />
              </Row>
              <Row label="Capture" hint="Game capture hooks the game directly (best performance). Display capture records a whole monitor.">
                <Seg value={s.recording.captureMode} onChange={set('recording', 'captureMode')} options={[{ value: 'game', label: 'Game' }, { value: 'desktop', label: 'Display' }]} />
              </Row>
              {(s.recording.captureMode === 'desktop' || s.recording.recordDesktopWhenNoGame) && (
                <Row label="Monitor">
                  <Select value={s.recording.monitorIndex} onChange={set('recording', 'monitorIndex')} options={(devices.monitors.length ? devices.monitors : [{ name: 'Primary monitor' }]).map((m, i) => ({ value: i, label: m.name }))} />
                </Row>
              )}
              <Row label="Clip the desktop when no game is running" hint="Keeps the replay buffer on with display capture outside of games.">
                <Toggle checked={s.recording.recordDesktopWhenNoGame} onChange={set('recording', 'recordDesktopWhenNoGame')} />
              </Row>
              <Row label="Record full sessions automatically" hint="Start a full recording when a game launches and stop when it closes. Use the bookmark hotkey to mark moments.">
                <Toggle checked={s.recording.autoRecordSessions} onChange={set('recording', 'autoRecordSessions')} />
              </Row>
              <Row label="File format" hint="MP4 is most compatible. MKV / Hybrid MP4 survive crashes during long recordings.">
                <Select value={s.recording.fileFormat} onChange={set('recording', 'fileFormat')} options={[{ value: 'mp4', label: 'MP4' }, { value: 'hybrid_mp4', label: 'Hybrid MP4 (OBS 30.2+)' }, { value: 'mkv', label: 'MKV' }]} />
              </Row>
            </Section>

            <PerformanceSection s={s} update={update} status={status} />

            <Section title="Hotkeys" id="hotkeys">
              {s.hotkeys.clips.map((c, i) => (
                <div className="set-row" key={i}>
                  <div className="label">
                    <input type="text" value={c.label} onChange={(e) => update({ hotkeys: { clips: s.hotkeys.clips.map((x, k) => (k === i ? { ...x, label: e.target.value } : x)) } })} style={{ fontWeight: 600, background: 'transparent', border: '1px solid transparent', padding: '2px 4px', marginLeft: -5 }} />
                    <small>Saves the last {fmtSecsLabel(c.seconds)}</small>
                  </div>
                  <div className="ctl">
                    <Select value={c.seconds} onChange={(v) => update({ hotkeys: { clips: s.hotkeys.clips.map((x, k) => (k === i ? { ...x, seconds: v } : x)) } })}
                      options={[5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600, 900, 1200].map((v) => ({ value: v, label: fmtSecsLabel(v) }))} />
                    <HotkeyInput value={c.accelerator} onChange={(v) => update({ hotkeys: { clips: s.hotkeys.clips.map((x, k) => (k === i ? { ...x, accelerator: v } : x)) } })} />
                    <button className="btn ghost sm icon" disabled={s.hotkeys.clips.length <= 1} onClick={() => update({ hotkeys: { clips: s.hotkeys.clips.filter((_, k) => k !== i) } })}><Icon name="x" size={14} /></button>
                  </div>
                </div>
              ))}
              <div className="set-row"><button className="btn sm" onClick={() => update({ hotkeys: { clips: [...s.hotkeys.clips, { accelerator: '', seconds: 30, label: `Clip ${s.hotkeys.clips.length + 1}` }] } })}><Icon name="plus" size={14} /> Add clip hotkey</button>
                <span className="dim" style={{ fontSize: 12 }}>The replay buffer automatically holds enough footage for your longest clip hotkey.</span></div>
              <Row label="Start / stop full recording"><HotkeyInput value={s.hotkeys.toggleRecording} onChange={set('hotkeys', 'toggleRecording')} /></Row>
              <Row label="Bookmark" hint="Marks a moment while recording a session (saves a 30s clip otherwise)."><HotkeyInput value={s.hotkeys.bookmark} onChange={set('hotkeys', 'bookmark')} /></Row>
              <Row label="Screenshot"><HotkeyInput value={s.hotkeys.screenshot} onChange={set('hotkeys', 'screenshot')} /></Row>
              <Row label="Mute / unmute microphone"><HotkeyInput value={s.hotkeys.toggleMic} onChange={set('hotkeys', 'toggleMic')} /></Row>
              <Row label="Pause / resume replay buffer"><HotkeyInput value={s.hotkeys.toggleReplay} onChange={set('hotkeys', 'toggleReplay')} /></Row>
            </Section>

            <Section title="Quality" id="quality">
              {s.performance.autoQuality ? (
                <div className="set-row">
                  <div className="label"><b>Resolution, frame rate and bitrate are set automatically</b><small>Auto quality picks the best settings for your memory limit. Turn it off under Performance &amp; memory to set them by hand.</small></div>
                  <button className="btn sm" onClick={() => document.getElementById('performance')?.scrollIntoView({ behavior: 'smooth' })}>Open</button>
                </div>
              ) : (<>
              <Row label="Resolution" >
                <Select value={s.quality.resolution} onChange={set('quality', 'resolution')} options={[{ value: 'native', label: 'Native (monitor)' }, { value: '2160', label: '2160p (4K)' }, { value: '1440', label: '1440p' }, { value: '1080', label: '1080p' }, { value: '720', label: '720p' }, { value: '480', label: '480p' }]} />
              </Row>
              <Row label="Frame rate">
                <Seg value={s.quality.fps} onChange={set('quality', 'fps')} options={[30, 60, 120, 144].map((f) => ({ value: f, label: String(f) }))} />
              </Row>
              <Row label="Rate control" hint="CBR = predictable size. CQP = constant quality (size varies).">
                <Seg value={s.quality.rateControl} onChange={set('quality', 'rateControl')} options={['CBR', 'VBR', 'CQP']} />
              </Row>
              {s.quality.rateControl !== 'CQP' ? (
                <Row label="Bitrate" hint={`${(s.quality.bitrateKbps / 1000).toFixed(0)} Mbps ≈ ${Math.round(s.quality.bitrateKbps * 60 / 8 / 1000)} MB per minute`} wide>
                  <Slider min={5000} max={150000} step={1000} value={s.quality.bitrateKbps} onChange={(v) => update({ quality: { bitrateKbps: v } })} />
                  <span className="mono" style={{ width: 70, textAlign: 'right' }}>{Math.round(s.quality.bitrateKbps / 1000)} Mbps</span>
                </Row>
              ) : (
                <Row label="Quality level (CQ)" hint="Lower = better quality, bigger files. 18–23 is typical." wide>
                  <Slider min={12} max={35} value={s.quality.cqLevel} onChange={(v) => update({ quality: { cqLevel: v } })} /><span className="mono" style={{ width: 30 }}>{s.quality.cqLevel}</span>
                </Row>
              )}
              </>)}
              <Row label="Codec" hint="HEVC/AV1 need a recent GPU. H.264 plays everywhere.">
                <Seg value={s.quality.codec} onChange={set('quality', 'codec')} options={[{ value: 'h264', label: 'H.264' }, { value: 'hevc', label: 'HEVC' }, { value: 'av1', label: 'AV1' }]} />
              </Row>
              <Row label="Encoder" hint={status?.encoder ? `In use: ${status.encoder}` : 'Hardware encoders barely affect FPS.'}>
                <Select value={s.quality.encoder} onChange={set('quality', 'encoder')} options={[{ value: 'auto', label: 'Auto-detect' }, { value: 'nvenc', label: 'NVIDIA NVENC' }, { value: 'amf', label: 'AMD AMF' }, { value: 'qsv', label: 'Intel Quick Sync' }, { value: 'x264', label: 'x264 (CPU)' }]} />
              </Row>
            </Section>

            <Section title="Audio" id="audio">
              <Row label="Desktop / game audio device">
                <Select style={{ maxWidth: 320 }} value={s.audio.desktopDevice} onChange={set('audio', 'desktopDevice')} options={[{ value: 'default', label: 'Default' }, ...devices.outputs.filter((d) => d.id !== 'default').map((d) => ({ value: d.id, label: d.name }))]} />
              </Row>
              <Row label="Desktop volume" wide><Slider min={0} max={2} step={0.05} value={s.audio.desktopVolume} onChange={(v) => update({ audio: { desktopVolume: v } })} /><span className="mono" style={{ width: 46 }}>{Math.round(s.audio.desktopVolume * 100)}%</span></Row>
              <Row label="Record game audio only" hint="Captures just the game process instead of everything you hear (no music/browser). Windows 10 2004+.">
                <Toggle checked={s.audio.gameAudioOnly} onChange={set('audio', 'gameAudioOnly')} />
              </Row>
              <Row label="Record microphone"><Toggle checked={s.audio.micEnabled} onChange={set('audio', 'micEnabled')} /></Row>
              <Row label="Microphone">
                <Select style={{ maxWidth: 320 }} value={s.audio.micDevice} onChange={set('audio', 'micDevice')} options={[{ value: 'default', label: 'Default' }, ...devices.inputs.filter((d) => d.id !== 'default').map((d) => ({ value: d.id, label: d.name }))]} />
              </Row>
              <Row label="Microphone volume" wide><Slider min={0} max={2} step={0.05} value={s.audio.micVolume} onChange={(v) => update({ audio: { micVolume: v } })} /><span className="mono" style={{ width: 46 }}>{Math.round(s.audio.micVolume * 100)}%</span></Row>
              <Row label="Noise suppression" hint="RNNoise removes keyboard clicks, fans and background noise from your mic."><Toggle checked={s.audio.noiseSuppression} onChange={set('audio', 'noiseSuppression')} /></Row>
              <Row label="Separate Discord track" hint="Records Discord voice on its own track so you can adjust it in the editor."><Toggle checked={s.audio.separateDiscordTrack} onChange={set('audio', 'separateDiscordTrack')} /></Row>
              <Row label="Audio bitrate" hint="Per track. Auto quality may lower it when memory is tight."><Select value={s.audio.audioBitrate} onChange={set('audio', 'audioBitrate')} options={[128, 160, 192, 256, 320].map((k) => ({ value: k, label: `${k} kbps` }))} /></Row>
              <div className="set-row dim" style={{ fontSize: 12 }}>Tracks in every recording: 1 = full mix · 2 = game/desktop · 3 = microphone · 4 = Discord</div>
            </Section>

            <Section title="Auto clipping" id="autoclip">
              <Row label="Auto-clip API" hint="Lets games, mods, scripts or a Stream Deck trigger clips on events (kills, wins…)."><Toggle checked={s.autoClip.enabled} onChange={set('autoClip', 'enabled')} /></Row>
              {s.autoClip.enabled && <>
                <Row label="Seconds before the event"><Select value={s.autoClip.secondsBefore} onChange={set('autoClip', 'secondsBefore')} options={[5, 10, 15, 20, 30, 45, 60].map((v) => ({ value: v, label: `${v}s` }))} /></Row>
                <Row label="Seconds after the event"><Select value={s.autoClip.secondsAfter} onChange={set('autoClip', 'secondsAfter')} options={[0, 2, 3, 5, 8, 10, 15].map((v) => ({ value: v, label: `${v}s` }))} /></Row>
                <Row label="Port"><input type="number" value={s.autoClip.apiPort} onChange={(e) => update({ autoClip: { apiPort: Number(e.target.value) } })} style={{ width: 100 }} /></Row>
                <div className="set-row" style={{ display: 'block' }}>
                  <div className="dim" style={{ fontSize: 12, marginBottom: 8 }}>Trigger from anything on this PC (while recording a session, events become bookmarks):</div>
                  <pre className="snippet">{`curl -X POST http://127.0.0.1:${s.autoClip.apiPort}/clip -d "{\\"event\\":\\"Ace\\",\\"before\\":20,\\"after\\":5}"
curl -X POST http://127.0.0.1:${s.autoClip.apiPort}/bookmark -d "{\\"label\\":\\"Clutch\\"}"
curl http://127.0.0.1:${s.autoClip.apiPort}/status`}</pre>
                </div>
              </>}
            </Section>

            <Section title="Overlay & sounds" id="overlay">
              <Row label="In-game notifications" hint="Small popup when a clip is saved (borderless/windowed games)."><Toggle checked={s.overlay.enabled} onChange={set('overlay', 'enabled')} /></Row>
              <Row label="Position"><Select value={s.overlay.position} onChange={set('overlay', 'position')} options={[{ value: 'top-right', label: 'Top right' }, { value: 'top-left', label: 'Top left' }, { value: 'bottom-right', label: 'Bottom right' }, { value: 'bottom-left', label: 'Bottom left' }]} /></Row>
              <Row label="Capture sounds" hint="Plays a sound when a clip is saved — works in every game."><Toggle checked={s.overlay.sound} onChange={set('overlay', 'sound')} /></Row>
              <Row label="Sound volume" wide><Slider min={0} max={1} step={0.05} value={s.overlay.soundVolume} onChange={(v) => update({ overlay: { soundVolume: v } })} /><span className="mono" style={{ width: 46 }}>{Math.round(s.overlay.soundVolume * 100)}%</span></Row>
            </Section>

            <Section title="Storage" id="storage">
              <Row label="Clip folder" hint={s.general.clipFolder}>
                <button className="btn sm" onClick={() => invoke('app:openPath', s.general.clipFolder)}><Icon name="folder" size={14} /> Open</button>
                <button className="btn sm" onClick={async () => { const f = await invoke('dialog:pickFolder', 'Choose clip folder'); if (f) update({ general: { clipFolder: f } }); }}>Change…</button>
              </Row>
              {stats && (
                <div className="set-row" style={{ display: 'block' }}>
                  <div className="row" style={{ marginBottom: 8 }}><b className="grow">{fmtSize(stats.used)} used by {stats.count} items</b>{stats.free != null && <span className="dim">{fmtSize(stats.free)} free of {fmtSize(stats.total)}</span>}</div>
                  {stats.total && <Bar value={(stats.total - stats.free) / stats.total} />}
                </div>
              )}
              <Row label="Automatic cleanup" hint="Delete the oldest clips (to the Recycle Bin) when the library exceeds the limit. Favorites, locked clips and clips in albums are never deleted.">
                <Toggle checked={s.storage.autoCleanup} onChange={set('storage', 'autoCleanup')} />
              </Row>
              {s.storage.autoCleanup && (
                <Row label="Storage limit" wide><Slider min={5} max={2000} step={5} value={s.storage.maxGB} onChange={(v) => update({ storage: { maxGB: v } })} /><span className="mono" style={{ width: 70 }}>{s.storage.maxGB} GB</span></Row>
              )}
              <Row label="Watch folders" hint="Automatically import recordings from OBS, ShadowPlay, etc.">
                <button className="btn sm" onClick={async () => { const f = await invoke('dialog:pickFolder', 'Folder to watch'); if (f && !s.watchFolders.includes(f)) update({ watchFolders: [...s.watchFolders, f] }); }}><Icon name="plus" size={14} /> Add folder</button>
              </Row>
              {s.watchFolders.map((f) => (
                <div className="set-row" key={f} style={{ minHeight: 40 }}>
                  <span className="mono grow" style={{ fontSize: 12 }}>{f}</span>
                  <button className="btn ghost sm icon" onClick={() => update({ watchFolders: s.watchFolders.filter((x) => x !== f) })}><Icon name="x" size={14} /></button>
                </div>
              ))}
              <Row label="Rescan library" hint="Find files added to the clip folder manually and remove missing ones.">
                <button className="btn sm" onClick={async () => { await invoke('library:scan'); toast({ title: 'Library rescanned' }); }}><Icon name="refresh" size={14} /> Rescan</button>
              </Row>
            </Section>

            <Section title="Compressor" id="compressor">
              <Row label="Default target size"><Seg value={s.compressor.defaultTargetMB} onChange={set('compressor', 'defaultTargetMB')} options={[20, 50, 500].map((m) => ({ value: m, label: `${m} MB` }))} /></Row>
              <Row label="Default encoder"><Select value={s.compressor.encoder} onChange={set('compressor', 'encoder')} options={[{ value: 'x264', label: 'CPU – best quality/size' }, { value: 'auto', label: 'GPU – fastest' }]} /></Row>
              <Row label="Two-pass encoding"><Toggle checked={s.compressor.twoPass} onChange={set('compressor', 'twoPass')} /></Row>
              <Row label="Smart downscale"><Toggle checked={s.compressor.autoDownscale} onChange={set('compressor', 'autoDownscale')} /></Row>
              <Row label="Audio bitrate"><Select value={s.compressor.audioKbps} onChange={set('compressor', 'audioKbps')} options={[64, 96, 128, 160, 192].map((k) => ({ value: k, label: `${k} kbps` }))} /></Row>
            </Section>

            <Section title="Editor" id="editor">
              <Row label="Text font" hint={s.editor.fontPath}>
                <button className="btn sm" onClick={async () => { const f = await invoke('dialog:pickFile', { title: 'Choose font', filters: [{ name: 'Fonts', extensions: ['ttf', 'otf'] }] }); if (f) update({ editor: { fontPath: f.replace(/\\/g, '/') } }); }}>Choose font…</button>
              </Row>
            </Section>

            <Section title="Updates" id="updates">
              <Row label={`Mettaloid ${info ? info.version : ''}`} hint={!upd ? '' : upd.status === 'dev' ? 'Updates are disabled in development builds.' : upd.status === 'checking' ? 'Checking for updates…' : upd.status === 'latest' ? 'You’re up to date.' : upd.status === 'available' ? `Version ${upd.available} is available.` : upd.status === 'downloading' ? `Downloading ${upd.available}… ${Math.round(upd.progress * 100)}%` : upd.status === 'ready' ? `Version ${upd.available} is ready to install.` : upd.status === 'error' ? `Update error: ${upd.error}` : ''}>
                {upd?.status === 'ready' ? <button className="btn primary sm" onClick={() => invoke('updater:install')}>Restart & update</button>
                  : upd?.status === 'available' && !s.updates.autoDownload ? <button className="btn primary sm" onClick={() => invoke('updater:download')}>Download</button>
                    : <button className="btn sm" disabled={upd?.status === 'dev' || upd?.status === 'checking' || upd?.status === 'downloading'} onClick={() => invoke('updater:check')}><Icon name="refresh" size={14} /> Check now</button>}
              </Row>
              {upd?.status === 'downloading' && <div className="set-row"><div className="grow"><Bar value={upd.progress} /></div></div>}
              <Row label="Check for updates automatically"><Toggle checked={s.updates.autoCheck} onChange={set('updates', 'autoCheck')} /></Row>
              <Row label="Download updates automatically" hint="Installed the next time Mettaloid restarts."><Toggle checked={s.updates.autoDownload} onChange={set('updates', 'autoDownload')} /></Row>
              <Row label="Update channel"><Seg value={s.updates.channel} onChange={set('updates', 'channel')} options={[{ value: 'latest', label: 'Stable' }, { value: 'beta', label: 'Beta' }]} /></Row>
            </Section>

            <Section title="Windows" id="windows">
              <Row label="Launch when Windows starts" hint="Starts minimized in the tray so it’s ready when you play."><Toggle checked={s.general.launchOnStartup} onChange={set('general', 'launchOnStartup')} /></Row>
              <Row label="Start minimized"><Toggle checked={s.general.startMinimized} onChange={set('general', 'startMinimized')} /></Row>
              <Row label="Closing the window keeps Mettaloid running in the tray"><Toggle checked={s.general.closeToTray} onChange={set('general', 'closeToTray')} /></Row>
            </Section>

            <Section title="Advanced" id="advanced">
              <Row label="Recording engine" hint={status ? `OBS Studio ${status.obsVersion || '?'} · ${status.obsState}${status.encoder ? ' · ' + status.encoder : ''}` : ''}>
                <button className="btn sm" onClick={() => invoke('recorder:restart')}><Icon name="refresh" size={14} /> Restart recorder</button>
              </Row>
              <Row label="Custom OBS folder" hint={(s.advanced && s.advanced.obsPath) || 'Using the bundled OBS Studio'}>
                <button className="btn sm" onClick={async () => { const f = await invoke('dialog:pickFolder', 'OBS Studio folder (contains bin\\64bit\\obs64.exe)'); if (f) update({ advanced: { obsPath: f } }); }}>Choose…</button>
                {s.advanced && s.advanced.obsPath && <button className="btn sm" onClick={() => update({ advanced: { obsPath: '' } })}>Reset</button>}
              </Row>
              <Row label="Hardware" hint={hw ? `GPU: ${hw.gpus.join(', ') || 'unknown'} · FFmpeg HW encoders: ${hw.ffmpegEncoders.join(', ') || 'none'}` : 'Detect GPUs and hardware encoders'}>
                <button className="btn sm" onClick={() => invoke('recorder:hw').then(setHw)}>Detect</button>
              </Row>
              <Row label="App data" hint={info?.userData}><button className="btn sm" onClick={() => invoke('app:openPath', info.userData)}><Icon name="folder" size={14} /> Open</button></Row>
            </Section>
          </div>
        </div>
      </div>
    </>
  );
}

const MEM_STEPS = [256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096, 6144, 8192, 12288, 16384];
const PRESET_HINT = {
  quality: 'Best image per bit. Uses the most GPU encoder time.',
  balanced: 'Recommended. Great quality with low overhead.',
  performance: 'Lighter on the GPU. Good for demanding games.',
  'max-performance': 'Lowest possible impact on game FPS.'
};

function PerformanceSection({ s, update, status }) {
  const perf = s.performance;
  const [sys, setSys] = useState(null);
  useEffect(() => { invoke('perf:stats').then(setSys).catch(() => {}); }, []);
  const maxMem = sys ? Math.max(1024, Math.floor((sys.totalMB * 0.6) / 256) * 256) : 16384;
  const steps = MEM_STEPS.filter((m) => m <= maxMem);
  const auto = !perf.replayMemoryMB;
  const current = auto ? (sys ? sys.autoMemoryMB : 2048) : perf.replayMemoryMB;
  const idx = Math.max(0, steps.findIndex((m) => m >= current));
  const [local, setLocal] = useState(idx);
  useEffect(() => setLocal(idx), [idx, steps.length]);
  const previewMem = auto ? 0 : steps[local];
  const plan = usePlan({ performance: { replayMemoryMB: previewMem } }, [previewMem, JSON.stringify(s)]);
  const fmt = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 ? 1 : 0)} GB` : `${mb} MB`);

  return (
    <Section title="Performance & memory" id="performance">
      <Row label="Replay memory limit" hint={sys ? `How much RAM the replay buffer may use. Your PC has ${fmt(sys.totalMB)} (${fmt(sys.freeMB)} free).` : ''}>
        <span className="dim" style={{ fontSize: 12.5 }}>Automatic</span>
        <Toggle checked={auto} onChange={(v) => update({ performance: { replayMemoryMB: v ? 0 : current } })} />
      </Row>
      {auto ? (
        <div className="set-row dim" style={{ fontSize: 12.5 }}>Using {fmt(current)} (about 12% of your RAM). Turn off Automatic to set your own limit.</div>
      ) : (
        <Row label="Limit" wide>
          <Slider min={0} max={steps.length - 1} value={local} onChange={setLocal} onCommit={(i) => update({ performance: { replayMemoryMB: steps[i] } })} />
          <span className="mono" style={{ width: 70, textAlign: 'right', fontWeight: 700, color: 'var(--accent)' }}>{fmt(steps[local])}</span>
        </Row>
      )}
      <Row label="Auto quality" hint="Calculates the best resolution, frame rate and bitrate that fit your replay length in the memory limit. Changes apply instantly.">
        <Toggle checked={perf.autoQuality} onChange={(v) => update({ performance: { autoQuality: v } })} />
      </Row>
      {perf.autoQuality && <>
        <Row label="Max resolution" hint="Auto quality never goes above this.">
          <Select value={perf.maxResolution} onChange={(v) => update({ performance: { maxResolution: v } })} options={[{ value: 'native', label: 'Native (monitor)' }, { value: '2160', label: '2160p' }, { value: '1440', label: '1440p' }, { value: '1080', label: '1080p' }, { value: '720', label: '720p' }]} />
        </Row>
        <Row label="Max frame rate">
          <Seg value={perf.maxFps} onChange={(v) => update({ performance: { maxFps: v } })} options={[30, 60, 120, 144, 240].map((f) => ({ value: f, label: String(f) }))} />
        </Row>
      </>}
      <PlanCard plan={plan} />
      <Row label="Encoder speed" hint={PRESET_HINT[perf.encoderPreset]}>
        <Seg value={perf.encoderPreset} onChange={(v) => update({ performance: { encoderPreset: v } })} options={[{ value: 'quality', label: 'Quality' }, { value: 'balanced', label: 'Balanced' }, { value: 'performance', label: 'Performance' }, { value: 'max-performance', label: 'Max perf' }]} />
      </Row>
      <Row label="Adaptive performance" hint="If the encoder starts dropping frames during a game, automatically switch to a faster encoder speed.">
        <Toggle checked={perf.adaptive} onChange={(v) => update({ performance: { adaptive: v } })} />
      </Row>
      <Row label="Recorder priority" hint="Above normal keeps recording smooth when the game maxes out your CPU.">
        <Select value={perf.obsPriority} onChange={(v) => update({ performance: { obsPriority: v } })} options={[{ value: 'High', label: 'High' }, { value: 'AboveNormal', label: 'Above normal' }, { value: 'Normal', label: 'Normal' }, { value: 'BelowNormal', label: 'Below normal' }]} />
      </Row>
      <Row label="Free memory in the background" hint="Unloads the Mettaloid window from RAM while it sits in the tray (~150–250 MB). Recording is unaffected.">
        <Toggle checked={perf.freeUiWhenHidden} onChange={(v) => update({ performance: { freeUiWhenHidden: v } })} />
      </Row>
      <Row label="Gentle exports" hint="Exports and compression run at low priority, and with fewer CPU threads while a game is running.">
        <Toggle checked={perf.lowPriorityJobs} onChange={(v) => update({ performance: { lowPriorityJobs: v } })} />
      </Row>
      <Row label="Game detection interval" hint="How often to look for newly launched games. While a game runs, only it is checked, which costs almost nothing.">
        <Select value={perf.detectionInterval} onChange={(v) => update({ performance: { detectionInterval: v } })} options={[1, 3, 5, 10].map((n) => ({ value: n, label: `${n}s` }))} />
      </Row>
      <div className="set-row" style={{ display: 'block' }}>
        <div className="dim" style={{ fontSize: 12, marginBottom: 8 }}>Live usage</div>
        <PerfStats status={status} />
      </div>
    </Section>
  );
}
