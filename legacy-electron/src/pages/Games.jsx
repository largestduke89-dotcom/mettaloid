import React, { useEffect, useMemo, useState } from 'react';
import { useApp } from '../App';
import { invoke, useSettings, fmtSecsLabel } from '../api';
import { Icon, GameIcon, Modal, Toggle, Select, Row, Section } from '../components/ui';
import { BUFFER_STEPS } from './Home';

export default function Games() {
  const { status, lib, go } = useApp();
  const [settings, update] = useSettings();
  const [data, setData] = useState(null);
  const [q, setQ] = useState('');
  const [edit, setEdit] = useState(null);
  const load = () => invoke('games:list').then(setData);
  useEffect(() => { load(); }, []);
  useEffect(() => { load(); }, [status && status.game && status.game.id]);

  const counts = useMemo(() => {
    const m = {};
    for (const c of lib.clips) if (c.game) m[c.game.id] = (m[c.game.id] || 0) + 1;
    return m;
  }, [lib.clips]);

  if (!data || !settings) return null;
  const all = [...data.custom.map((g) => ({ ...g, custom: true })), ...data.builtin];
  const list = all.filter((g) => !q || g.name.toLowerCase().includes(q.toLowerCase()));
  const disabled = new Set(settings.games.disabled || []);
  const profiles = settings.games.profiles;

  return (
    <>
      <div className="topbar">
        <h1>Games</h1>
        <span className="dim">{all.length} games recognized automatically</span>
        <span className="grow" />
        <input type="search" placeholder="Search games" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 220 }} />
        <button className="btn primary sm" onClick={async () => { await invoke('games:addCustom'); load(); }}><Icon name="plus" size={14} /> Add a game</button>
      </div>
      <div className="content">
        {status && status.game && (
          <div className="card card-pad row" style={{ marginBottom: 18, borderColor: 'rgba(34,197,94,.45)' }}>
            <GameIcon name={status.game.name} size={48} />
            <div className="grow">
              <div className="h2">{status.game.name}</div>
              <div className="dim">Running now · {status.game.exe}{profiles[status.game.id]?.enabled ? ' · custom profile active' : ''}</div>
            </div>
            <button className="btn" onClick={() => setEdit(all.find((g) => g.id === status.game.id) || status.game)}>Recording profile</button>
          </div>
        )}
        <div className="dim" style={{ marginBottom: 14, fontSize: 12.5 }}>
          Not listed? Click <b>Add a game</b> and pick its .exe. Games with a custom profile record with their own resolution, frame rate, replay length and mic settings.
        </div>
        <div className="game-grid">
          {list.map((g) => (
            <div key={g.id} className={`game-card ${status?.game?.id === g.id ? 'live' : ''}`} onClick={() => setEdit(g)} style={disabled.has(g.id) ? { opacity: 0.45 } : undefined}>
              <GameIcon name={g.name} />
              <div className="grow" style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 650, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{g.name}</div>
                <div className="dim" style={{ fontSize: 12 }}>
                  {g.custom ? 'Custom' : g.category}{counts[g.id] ? ` · ${counts[g.id]} clips` : ''}{profiles[g.id]?.enabled ? ' · profile' : ''}{disabled.has(g.id) ? ' · ignored' : ''}
                </div>
              </div>
              {status?.game?.id === g.id && <span className="badge live"><span className="dot" />Live</span>}
            </div>
          ))}
        </div>
      </div>
      {edit && <ProfileModal game={edit} settings={settings} update={update} onClose={() => { setEdit(null); load(); }} onClips={() => go(`/library`)} />}
    </>
  );
}

function ProfileModal({ game, settings, update, onClose }) {
  const existing = settings.games.profiles[game.id] || {};
  const [p, setP] = useState({
    enabled: false, resolution: settings.performance.autoQuality ? settings.performance.maxResolution : settings.quality.resolution, fps: settings.performance.autoQuality ? settings.performance.maxFps : settings.quality.fps,
    replaySeconds: settings.recording.replaySeconds, micEnabled: settings.audio.micEnabled, ...existing
  });
  const disabled = new Set(settings.games.disabled || []);
  const [ignored, setIgnored] = useState(disabled.has(game.id));
  const save = async () => {
    const dis = new Set(settings.games.disabled || []);
    if (ignored) dis.add(game.id); else dis.delete(game.id);
    await update({ games: { profiles: { ...settings.games.profiles, [game.id]: p }, disabled: [...dis] } });
    onClose();
  };
  const removeCustom = async () => {
    await update({ games: { custom: settings.games.custom.filter((g) => g.id !== game.id) } });
    onClose();
  };
  return (
    <Modal title={game.name} onClose={onClose} width={560}
      footer={<>{game.custom && <button className="btn danger" onClick={removeCustom} style={{ marginRight: 'auto' }}>Remove game</button>}<button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={save}>Save</button></>}>
      <div className="dim" style={{ fontSize: 12, marginBottom: 12 }}>Executable: {(Array.isArray(game.exe) ? game.exe : [game.exe]).join(', ')}</div>
      <Section>
        <Row label="Don't record this game" hint="Mettaloid will ignore it when it launches."><Toggle checked={ignored} onChange={setIgnored} /></Row>
        <Row label="Use a custom recording profile"><Toggle checked={p.enabled} onChange={(v) => setP({ ...p, enabled: v })} /></Row>
        {p.enabled && <>
          <Row label={settings.performance.autoQuality ? 'Max resolution' : 'Resolution'} hint={settings.performance.autoQuality ? 'Auto quality picks the best settings up to this.' : ''}><Select value={p.resolution} onChange={(v) => setP({ ...p, resolution: v })} options={[{ value: 'native', label: 'Native' }, { value: '2160', label: '2160p' }, { value: '1440', label: '1440p' }, { value: '1080', label: '1080p' }, { value: '720', label: '720p' }]} /></Row>
          <Row label={settings.performance.autoQuality ? 'Max frame rate' : 'Frame rate'}><Select value={p.fps} onChange={(v) => setP({ ...p, fps: v })} options={[30, 60, 120, 144, 165, 240].map((f) => ({ value: f, label: `${f} fps` }))} /></Row>
          <Row label="Replay length"><Select value={p.replaySeconds} onChange={(v) => setP({ ...p, replaySeconds: v })} options={BUFFER_STEPS.map((s) => ({ value: s, label: fmtSecsLabel(s) }))} /></Row>
          <Row label="Record microphone"><Toggle checked={p.micEnabled} onChange={(v) => setP({ ...p, micEnabled: v })} /></Row>
        </>}
      </Section>
    </Modal>
  );
}
