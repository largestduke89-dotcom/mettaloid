import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useApp } from '../App';
import { invoke, on, mediaUrl, fmtTime, fmtDur, fmtDate, useSettings } from '../api';
import { Icon, Bar, Modal, Row, Section, Select, Seg, Slider, Toggle } from '../components/ui';

const TABS = [
  { id: 'results', label: 'Results', icon: 'search' },
  { id: 'finders', label: 'Saved finders', icon: 'target' },
  { id: 'scan', label: 'Scan library', icon: 'scan' },
  { id: 'settings', label: 'Settings', icon: 'settings' }
];

function useFindFrame() {
  const [jobs, setJobs] = useState([]);
  const [finders, setFinders] = useState([]);
  const [thumbs, setThumbs] = useState({});
  const loadFinders = useCallback(() => invoke('ff:finders').then(setFinders), []);
  useEffect(() => {
    invoke('ff:jobs').then(setJobs);
    loadFinders();
    const offs = [
      on('ff:job', (j) => setJobs((prev) => {
        const i = prev.findIndex((x) => x.id === j.id);
        if (i === -1) return [j, ...prev];
        const n = prev.slice();
        n[i] = j;
        return n;
      })),
      on('ff:thumb', ({ matchId, thumb }) => setThumbs((t) => ({ ...t, [matchId]: thumb }))),
      on('ff:changed', () => { clearTimeout(useFindFrame.t); useFindFrame.t = setTimeout(loadFinders, 300); })
    ];
    return () => offs.forEach((f) => f());
  }, [loadFinders]);
  return { jobs, setJobs, finders, thumbs, loadFinders };
}

/** Build editor montage segments from matches (merging overlaps per clip). */
function montageQuery(groups, pad) {
  const segs = [];
  for (const g of groups) {
    const ranges = g.matches.map((m) => [Math.max(0, m.start - pad.before), m.end + pad.after]).sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const r of ranges) {
      const last = merged[merged.length - 1];
      if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
      else merged.push([...r]);
    }
    merged.forEach(([s, e]) => segs.push(`${g.clipId}:${s.toFixed(2)}:${e.toFixed(2)}`));
  }
  return segs.join(',');
}

function groupMatches(matches) {
  const m = new Map();
  for (const x of matches) {
    if (!m.has(x.clipId)) m.set(x.clipId, []);
    m.get(x.clipId).push(x);
  }
  return [...m.entries()].map(([clipId, list]) => ({ clipId, matches: list.sort((a, b) => a.t - b.t) }));
}

function MatchGrid({ groups, thumbs, finders, onRemove, pad }) {
  const { lib, go } = useApp();
  const fmap = Object.fromEntries((finders || []).map((f) => [f.id, f]));
  if (!groups.length) return <div className="empty"><div><div className="big">🔎</div><b>No matches yet</b></div></div>;
  return groups.map((g) => {
    const clip = lib.clips.find((c) => c.id === g.clipId);
    if (!clip) return null;
    return (
      <div key={g.clipId} style={{ marginBottom: 20 }}>
        <div className="row" style={{ marginBottom: 8 }}>
          <b style={{ cursor: 'pointer' }} onClick={() => go('/clip/' + clip.id)}>{clip.title}</b>
          <span className="dim" style={{ fontSize: 12 }}>{clip.game ? clip.game.name : 'Desktop'} · {fmtDate(clip.createdAt)} · {g.matches.length} match{g.matches.length > 1 ? 'es' : ''}</span>
        </div>
        <div className="grid" style={{ '--card-w': '210px', gap: 12, marginBottom: 0 }}>
          {g.matches.map((m) => {
            const th = thumbs[m.id] || m.thumb;
            const f = fmap[m.finderId];
            return (
              <div key={m.id + m.t} className="clip-card" onClick={() => go(`/clip/${clip.id}?t=${Math.max(0, m.t - 1).toFixed(2)}`)}>
                <div className="clip-thumb">
                  {th ? <img src={mediaUrl(th)} alt="" /> : clip.thumb ? <img src={mediaUrl(clip.thumb)} alt="" style={{ opacity: 0.5 }} /> : null}
                  <span className="clip-dur">{fmtTime(m.t)}</span>
                  <span className="clip-type badge" style={{ background: 'rgba(0,0,0,.75)', color: m.score >= 0.9 ? '#4ade80' : m.score >= 0.6 ? 'var(--accent-2)' : '#fb923c' }}>{Math.round(m.score * 100)}%</span>
                </div>
                <div className="row" style={{ paddingTop: 6, gap: 6 }}>
                  {f && <span className="dot" style={{ color: f.color || 'var(--accent)' }} />}
                  <span className="dim grow" style={{ fontSize: 11.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f ? f.name : m.method}{m.end - m.start > 0.5 ? ` · ${fmtDur(m.end - m.start)}` : ''}</span>
                  <button className="btn ghost sm" title="Open in editor around this match" onClick={(e) => { e.stopPropagation(); go(`/editor/${clip.id}?start=${Math.max(0, m.start - pad.before).toFixed(2)}&end=${Math.min(clip.duration, m.end + pad.after).toFixed(2)}`); }}>Clip it</button>
                  {onRemove && <button className="btn ghost sm icon" title="Not a match" onClick={(e) => { e.stopPropagation(); onRemove(m); }}><Icon name="x" size={13} /></button>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  });
}

export default function FindFrame() {
  const { route, go } = useApp();
  const ff = useFindFrame();
  const [settings, update] = useSettings();
  const [tab, setTab] = useState(route.query.get('job') ? 'results' : route.query.get('tab') || 'results');
  const [jobId, setJobId] = useState(route.query.get('job'));
  useEffect(() => {
    if (route.query.get('job')) { setTab('results'); setJobId(route.query.get('job')); }
    else if (route.query.get('tab')) setTab(route.query.get('tab'));
  }, [route.query.get('job'), route.query.get('tab')]);
  if (!settings) return null;
  const pad = { before: settings.findFrame.padBefore, after: settings.findFrame.padAfter };
  const running = ff.jobs.filter((j) => j.status === 'running').length;
  return (
    <>
      <div className="topbar">
        <h1>Find Frame</h1>
        <div className="seg" style={{ marginLeft: 10 }}>
          {TABS.map((t) => <button key={t.id} className={tab === t.id ? 'on' : ''} onClick={() => setTab(t.id)}><Icon name={t.icon} size={13} style={{ verticalAlign: -2 }} /> {t.label}{t.id === 'results' && running ? ` (${running})` : t.id === 'finders' && ff.finders.length ? ` (${ff.finders.length})` : ''}</button>)}
        </div>
      </div>
      <div className="content" style={tab === 'results' ? { padding: 0, overflow: 'hidden' } : undefined}>
        {tab === 'results' && <ResultsTab ff={ff} jobId={jobId} setJobId={setJobId} pad={pad} />}
        {tab === 'finders' && <FindersTab ff={ff} pad={pad} onScan={(id) => { setJobId(id); setTab('results'); }} />}
        {tab === 'scan' && <ScanTab ff={ff} onStarted={(id) => { setJobId(id); setTab('results'); }} />}
        {tab === 'settings' && <SettingsTab s={settings} update={update} />}
      </div>
    </>
  );
}

// ---------------------------------------------------------------- results
function ResultsTab({ ff, jobId, setJobId, pad }) {
  const { go, lib, toast } = useApp();
  const job = ff.jobs.find((j) => j.id === jobId) || ff.jobs[0];
  const groups = useMemo(() => (job ? groupMatches(job.matches) : []), [job && job.matches]);
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '280px minmax(0,1fr)', height: '100%' }}>
      <div className="lib-side">
        <div className="row" style={{ padding: '0 6px 10px' }}>
          <div className="h3 grow">Searches</div>
          {ff.jobs.some((j) => j.status !== 'running') && <button className="btn ghost sm" onClick={() => invoke('ff:clearJobs').then(() => invoke('ff:jobs')).then(ff.setJobs)}>Clear</button>}
        </div>
        {ff.jobs.length === 0 && <div className="dim" style={{ padding: 10, fontSize: 12.5 }}>Open a clip, pause it and press <b>Find Frame</b> (or <kbd>B</kbd>) to search. Saved finders can scan your whole library.</div>}
        {ff.jobs.map((j) => (
          <div key={j.id} className={`nav-item ${job && j.id === job.id ? 'active' : ''}`} style={{ display: 'block' }} onClick={() => setJobId(j.id)}>
            <div className="row" style={{ gap: 6 }}>
              {j.finders[0] && <img src={mediaUrl(j.finders[0].image)} alt="" style={{ width: 28, height: 28, objectFit: 'contain', background: '#000', borderRadius: 5 }} />}
              <span className="grow" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{j.label}</span>
              <span className="count">{j.matchCount}</span>
            </div>
            <div className="dim" style={{ fontSize: 11.5, marginTop: 4 }}>
              {j.status === 'running' ? `${j.paused ? 'Paused (game running)' : 'Scanning'} ${j.done}/${j.total}` : j.status === 'done' ? `Done · ${j.total} clip${j.total === 1 ? '' : 's'}${j.skipped ? ` (${j.skipped} already scanned)` : ''}` : j.status}
            </div>
            {j.status === 'running' && <div style={{ marginTop: 5 }}><Bar value={j.total ? j.done / j.total : 0} /></div>}
          </div>
        ))}
      </div>
      <div style={{ overflow: 'auto', padding: '18px 22px 60px' }}>
        {!job ? <div className="empty"><div><div className="big">🎯</div><b>No searches yet</b></div></div> : (
          <>
            <div className="row wrap" style={{ marginBottom: 14 }}>
              <div className="row" style={{ gap: 6 }}>{job.finders.map((f) => <img key={f.id} src={mediaUrl(f.image)} alt="" title={f.name} style={{ height: 54, maxWidth: 120, objectFit: 'contain', background: '#000', borderRadius: 8, border: '1px solid var(--line-2)' }} />)}</div>
              <div className="grow">
                <div className="h2">{job.label}</div>
                <div className="dim" style={{ fontSize: 12.5 }}>
                  {job.matchCount} match{job.matchCount === 1 ? '' : 'es'} in {groups.length} clip{groups.length === 1 ? '' : 's'}
                  {job.status === 'running' && ` · ${job.done}/${job.total} clips · ${job.fps || 0} frames/s`}
                  {job.finishedAt && ` · took ${fmtDur((job.finishedAt - job.startedAt) / 1000)}`}
                </div>
              </div>
              {job.status === 'running' && <button className="btn sm" onClick={() => invoke('ff:cancel', job.id)}>Cancel</button>}
              {job.reference && job.status !== 'running' && (
                <button className="btn sm" onClick={async () => { await invoke('ff:createFinder', { ...job.reference, name: job.label, settings: job.reference.settings }); toast({ title: 'Saved as finder', subtitle: job.label }); }}><Icon name="save" size={14} /> Save as finder</button>
              )}
              {groups.length > 0 && <button className="btn sm primary" onClick={() => go(`/editor/montage?seg=${montageQuery(groups, pad)}`)}><Icon name="merge" size={14} /> Montage of matches</button>}
            </div>
            {job.status === 'running' && Object.entries(job.current || {}).map(([vid, c]) => {
              const clip = lib.clips.find((x) => x.id === vid);
              const best = c.scores ? Math.max(0, ...Object.values(c.scores)) : 0;
              return (
                <div key={vid} className="card" style={{ padding: 12, marginBottom: 10 }}>
                  <div className="row" style={{ marginBottom: 6, fontSize: 12.5 }}>
                    <b className="grow" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{clip ? clip.title : vid}</b>
                    <span className="dim">{fmtDur(c.t)} / {fmtDur(c.duration)}</span>
                    <span className="dim" title="Live similarity of the current frame">match {Math.round(best * 100)}%</span>
                  </div>
                  <Bar value={c.duration ? c.t / c.duration : 0} />
                </div>
              );
            })}
            {job.errors && job.errors.length > 0 && <div style={{ color: '#fca5a5', fontSize: 12.5, marginBottom: 10 }}>{job.errors.slice(-3).join(' · ')}</div>}
            <MatchGrid groups={groups} thumbs={ff.thumbs} finders={ff.finders} pad={pad} />
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- finders
function FindersTab({ ff, pad, onScan }) {
  const [view, setView] = useState(null);
  const [edit, setEdit] = useState(null);
  const [results, setResults] = useState([]);
  const { toast } = useApp();
  const loadResults = useCallback(() => view && invoke('ff:results', view).then(setResults), [view]);
  useEffect(() => { loadResults(); }, [view, ff.finders]);
  const finder = ff.finders.find((f) => f.id === view);
  if (!ff.finders.length) {
    return <div className="empty"><div><div className="big">🎯</div><b>No saved finders</b><div className="dim" style={{ marginTop: 6 }}>Open any clip, pause, press <b>Find Frame</b>, draw a box and choose <b>Save finder</b>.<br />Saved finders can scan your whole library and automatically check every new clip.</div></div></div>;
  }
  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      <div className="grid" style={{ '--card-w': '260px' }}>
        {ff.finders.map((f) => (
          <div key={f.id} className="card" style={{ padding: 12, borderColor: view === f.id ? 'var(--accent)' : undefined, cursor: 'pointer' }} onClick={() => setView(view === f.id ? null : f.id)}>
            <div style={{ height: 110, background: '#000', borderRadius: 8, display: 'grid', placeItems: 'center', overflow: 'hidden', marginBottom: 10 }}>
              <img src={mediaUrl(f.image)} alt="" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
            </div>
            <div className="row" style={{ gap: 6 }}>
              <span className="dot" style={{ color: f.color }} />
              <b className="grow" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.name}</b>
              <span className="badge">{(f.settings && f.settings.method) || 'auto'}</span>
            </div>
            <div className="dim" style={{ fontSize: 12, margin: '4px 0 10px' }}>{f.matchCount} match{f.matchCount === 1 ? '' : 'es'} in {f.clipCount} clip{f.clipCount === 1 ? '' : 's'}{f.tag ? ` · #${f.tag}` : ''}</div>
            <div className="row" onClick={(e) => e.stopPropagation()}>
              <button className="btn sm primary" onClick={async () => { const j = await invoke('ff:search', { finderIds: [f.id], clipIds: 'all' }); onScan(j.id); }}><Icon name="scan" size={13} /> Scan all</button>
              <button className="btn sm" onClick={() => setEdit(f)}>Edit</button>
              <span className="grow" />
              <label className="row" style={{ gap: 6, fontSize: 11.5, cursor: 'pointer' }} title="Automatically scan new clips"><Toggle checked={f.autoScan} onChange={(v) => invoke('ff:updateFinder', f.id, { autoScan: v })} /> Auto</label>
            </div>
          </div>
        ))}
      </div>
      {finder && (
        <div style={{ marginTop: 10 }}>
          <div className="row" style={{ marginBottom: 14 }}>
            <div className="h2 grow">Matches for “{finder.name}”</div>
            {results.length > 0 && <button className="btn sm primary" onClick={() => (location.hash = `/editor/montage?seg=${montageQuery(results, pad)}`)}><Icon name="merge" size={14} /> Montage</button>}
            <button className="btn sm" onClick={async () => { const j = await invoke('ff:search', { finderIds: [finder.id], clipIds: 'all', rescan: true }); onScan(j.id); }}>Rescan everything</button>
            <button className="btn sm danger" onClick={async () => { await invoke('ff:clearResults', finder.id); toast({ title: 'Results cleared' }); loadResults(); }}>Clear results</button>
          </div>
          <MatchGrid groups={results} thumbs={ff.thumbs} finders={ff.finders} pad={pad} onRemove={async (m) => { await invoke('ff:removeMatch', finder.id, m.clipId, m.t); loadResults(); }} />
        </div>
      )}
      {edit && <EditFinder finder={edit} onClose={() => setEdit(null)} onDeleted={() => { setEdit(null); setView(null); }} />}
    </div>
  );
}

function EditFinder({ finder, onClose, onDeleted }) {
  const [f, setF] = useState({ name: finder.name, tag: finder.tag || '', color: finder.color || '#f5b70a', settings: { ...finder.settings } });
  const s = f.settings;
  const setS = (patch) => setF({ ...f, settings: { ...s, ...patch } });
  return (
    <Modal title="Edit finder" onClose={onClose} width={560}
      footer={<>
        <button className="btn danger" style={{ marginRight: 'auto' }} onClick={async () => { await invoke('ff:deleteFinder', finder.id); onDeleted(); }}><Icon name="trash" size={14} /> Delete</button>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={async () => { await invoke('ff:updateFinder', finder.id, f); onClose(); }}>Save</button>
      </>}>
      <div className="col" style={{ gap: 12 }}>
        <div className="row">
          <div className="field grow"><label>Name</label><input type="text" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
          <div className="field"><label>Color</label><input type="color" value={f.color} onChange={(e) => setF({ ...f, color: e.target.value })} style={{ width: 54, height: 34, padding: 2, background: 'var(--bg-3)', border: '1px solid var(--line-2)', borderRadius: 8 }} /></div>
        </div>
        <div className="field"><label>Tag clips with</label><input type="text" placeholder={f.name} value={f.tag} onChange={(e) => setF({ ...f, tag: e.target.value })} /></div>
        <div className="field"><label>Method</label><Seg value={s.method || 'auto'} onChange={(v) => setS({ method: v })} options={[{ value: 'auto', label: 'Auto' }, { value: 'template', label: 'Exact look' }, { value: 'features', label: 'Features' }]} /></div>
        <div className="field"><label>Feature threshold <em>{s.threshold ?? 'default'}{s.threshold !== undefined ? '%' : ''}</em></label><Slider min={5} max={90} value={s.threshold ?? 30} onChange={(v) => setS({ threshold: v })} /></div>
        <div className="field"><label>Template threshold <em>{s.templateThreshold !== undefined ? `${Math.round(s.templateThreshold * 100)}%` : 'default'}</em></label><Slider min={0.5} max={0.99} step={0.01} value={s.templateThreshold ?? 0.8} onChange={(v) => setS({ templateThreshold: v })} /></div>
        <div className="field"><label>Search area</label><Seg value={s.searchArea || 'anywhere'} onChange={(v) => setS({ searchArea: v })} options={[{ value: 'anywhere', label: 'Anywhere' }, { value: 'near', label: 'Same spot' }]} /></div>
        <div className="field"><label>Preprocessing</label>
          <div className="row wrap" style={{ gap: 14 }}>
            <label className="row" style={{ cursor: 'pointer' }}><Toggle checked={!!s.boostContrast} onChange={(v) => setS({ boostContrast: v })} /> Boost contrast</label>
            <label className="row" style={{ cursor: 'pointer' }}><Toggle checked={!!s.equalize} onChange={(v) => setS({ equalize: v })} /> Equalize</label>
            <label className="row" style={{ cursor: 'pointer' }}><Toggle checked={!!s.edges} onChange={(v) => setS({ edges: v })} /> Edges only</label>
          </div>
        </div>
        <div className="dim" style={{ fontSize: 12 }}>Changing match settings marks all clips as unscanned for this finder, so the next scan rechecks them.</div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- scan library
function ScanTab({ ff, onStarted }) {
  const { lib, toast } = useApp();
  const [sel, setSel] = useState(new Set());
  const [game, setGame] = useState('');
  const [types, setTypes] = useState(['clip', 'session', 'export', 'import']);
  const [since, setSince] = useState(0);
  const [favorites, setFavorites] = useState(false);
  const [rescan, setRescan] = useState(false);
  useEffect(() => { if (!sel.size && ff.finders.length) setSel(new Set(ff.finders.map((f) => f.id))); }, [ff.finders.length]);
  const games = [...new Set(lib.clips.map((c) => (c.game ? c.game.name : 'Desktop')))].sort();
  const sinceTs = since ? Date.now() - since * 864e5 : 0;
  const count = lib.clips.filter((c) => c.type !== 'screenshot' && types.includes(c.type) && (!game || (c.game ? c.game.name : 'Desktop') === game) && (!sinceTs || c.createdAt >= sinceTs) && (!favorites || c.favorite)).length;
  const totalMin = lib.clips.filter((c) => c.type !== 'screenshot' && types.includes(c.type) && (!game || (c.game ? c.game.name : 'Desktop') === game) && (!sinceTs || c.createdAt >= sinceTs) && (!favorites || c.favorite)).reduce((t, c) => t + (c.duration || 0), 0) / 60;
  const start = async () => {
    const j = await invoke('ff:search', { finderIds: [...sel], clipIds: 'all', rescan, filters: { game: game || null, types, since: sinceTs || null, favorites } });
    toast({ title: 'Library scan started', subtitle: `${count} clips · ${sel.size} finder${sel.size > 1 ? 's' : ''}` });
    onStarted(j.id);
  };
  if (!ff.finders.length) return <div className="empty"><div><div className="big">📚</div><b>Save a finder first</b><div className="dim" style={{ marginTop: 6 }}>Draw a box on any paused clip and choose “Save finder”.</div></div></div>;
  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <Section title="Finders to use">
        {ff.finders.map((f) => (
          <div className="set-row" key={f.id} style={{ cursor: 'pointer' }} onClick={() => { const n = new Set(sel); n.has(f.id) ? n.delete(f.id) : n.add(f.id); setSel(n); }}>
            <Toggle checked={sel.has(f.id)} onChange={() => {}} />
            <img src={mediaUrl(f.image)} alt="" style={{ width: 64, height: 40, objectFit: 'contain', background: '#000', borderRadius: 6 }} />
            <div className="label"><b>{f.name}</b><small>{f.matchCount} matches so far · {(f.settings && f.settings.method) || 'auto'}</small></div>
          </div>
        ))}
      </Section>
      <Section title="Clips">
        <Row label="Game"><Select value={game} onChange={setGame} options={[{ value: '', label: 'All games' }, ...games.map((g) => ({ value: g, label: g }))]} /></Row>
        <Row label="Types">
          <div className="seg">
            {[['clip', 'Clips'], ['session', 'Sessions'], ['export', 'Edited'], ['import', 'Imported']].map(([v, l]) => (
              <button key={v} className={types.includes(v) ? 'on accent' : ''} onClick={() => setTypes(types.includes(v) ? types.filter((x) => x !== v) : [...types, v])}>{l}</button>
            ))}
          </div>
        </Row>
        <Row label="Captured"><Seg value={since} onChange={setSince} options={[{ value: 0, label: 'Any time' }, { value: 1, label: '24 h' }, { value: 7, label: '7 days' }, { value: 30, label: '30 days' }]} /></Row>
        <Row label="Favorites only"><Toggle checked={favorites} onChange={setFavorites} /></Row>
        <Row label="Rescan clips already scanned" hint="Off = only clips a finder hasn't checked yet (much faster)."><Toggle checked={rescan} onChange={setRescan} /></Row>
      </Section>
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <span className="dim">{count} clips · {Math.round(totalMin)} min of video</span>
        <button className="btn primary lg" disabled={!sel.size || !count} onClick={start}><Icon name="scan" /> Scan {count} clip{count === 1 ? '' : 's'}</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- settings
function SettingsTab({ s, update }) {
  const c = s.findFrame;
  const set = (k) => (v) => update({ findFrame: { [k]: v } });
  const num = (k, min, max, step = 1, fmt = (v) => v) => (
    <><Slider min={min} max={max} step={step} value={c[k]} onChange={(v) => update({ findFrame: { [k]: v } })} /><span className="mono" style={{ width: 64, textAlign: 'right' }}>{fmt(c[k])}</span></>
  );
  return (
    <div style={{ maxWidth: 860, margin: '0 auto' }}>
      <Section title="Matching">
        <Row label="Default method" hint="Exact look = template matching (best for HUD icons/text at a fixed size). Features = FindFrame's ORB keypoints (handles zoom/rotation/lighting). Auto picks per reference.">
          <Seg value={c.method} onChange={set('method')} options={[{ value: 'auto', label: 'Auto' }, { value: 'template', label: 'Exact look' }, { value: 'features', label: 'Features' }]} />
        </Row>
        <Row label="Feature match threshold" hint="% of the reference's keypoints that must match. Raise it if you get false positives." wide>{num('threshold', 5, 90, 1, (v) => `${v}%`)}</Row>
        <Row label="Template match threshold" hint="How closely pixels must match (correlation)." wide>{num('templateThreshold', 0.5, 0.99, 0.01, (v) => `${Math.round(v * 100)}%`)}</Row>
        <Row label="Keypoints per reference" hint="More = more robust, slower (FindFrame uses 1000)." wide>{num('nfeatures', 250, 4000, 50)}</Row>
        <Row label="Ratio test" hint="Lowe ratio for accepting a keypoint match. Lower = stricter." wide>{num('ratio', 0.5, 0.95, 0.01, (v) => v.toFixed(2))}</Row>
        <Row label="Verify geometry" hint="RANSAC check that matched keypoints form a consistent shape — removes most false positives."><Toggle checked={c.verifyGeometry} onChange={set('verifyGeometry')} /></Row>
        <Row label="Minimum inliers" wide>{num('minInliers', 4, 40)}</Row>
        <Row label="Minimum good matches" wide>{num('minMatches', 4, 60)}</Row>
        <Row label="Auto: keypoints needed for feature mode" hint="References with fewer keypoints use template matching." wide>{num('autoMinKeypoints', 10, 300, 5)}</Row>
        <Row label="Keypoint matcher"><Seg value={c.matcher} onChange={set('matcher')} options={[{ value: 'bf', label: 'Brute force (exact)' }, { value: 'flann', label: 'FLANN (fast)' }]} /></Row>
        <Row label="Default search area"><Seg value={c.searchArea} onChange={set('searchArea')} options={[{ value: 'anywhere', label: 'Anywhere' }, { value: 'near', label: 'Same spot' }]} /></Row>
        <Row label="Same-spot margin" hint="How far from the original box to look (fraction of the screen)." wide>{num('nearMargin', 0.02, 0.5, 0.01, (v) => `${Math.round(v * 100)}%`)}</Row>
        <Row label="Template scales" hint="Sizes to try, relative to the box. Add more if the object changes size."><input type="text" value={c.scales} onChange={(e) => update({ findFrame: { scales: e.target.value } })} style={{ width: 200 }} /></Row>
      </Section>
      <Section title="Image preprocessing">
        <Row label="Boost contrast" hint="Like FindFrame's option — helps with dark or washed-out footage."><Toggle checked={c.boostContrast} onChange={set('boostContrast')} /></Row>
        {c.boostContrast && <Row label="Contrast amount" wide>{num('contrast', 1.2, 4, 0.1, (v) => `${v.toFixed(1)}x`)}</Row>}
        <Row label="Equalize (CLAHE)" hint="Normalizes lighting differences between clips."><Toggle checked={c.equalize} onChange={set('equalize')} /></Row>
        <Row label="Edges only" hint="Match outlines instead of colors — ignores background/brightness changes behind HUD elements."><Toggle checked={c.edges} onChange={set('edges')} /></Row>
        <Row label="Blur" hint="Reduces noise/compression artifacts." wide>{num('blur', 0, 5)}</Row>
      </Section>
      <Section title="Scanning speed">
        <Row label="Frames analysed per second" hint="5 catches anything on screen for 0.2 s or longer. 0 = every frame (slow, most precise)." wide>{num('scanFps', 0, 30, 1, (v) => (v ? `${v} fps` : 'all'))}</Row>
        <Row label="Analysis resolution" hint="Frames are downscaled to this width first. Lower is faster.">
          <Select value={c.scanWidth} onChange={set('scanWidth')} options={[{ value: 640, label: '640 px (fastest)' }, { value: 960, label: '960 px' }, { value: 1280, label: '1280 px' }, { value: 1920, label: '1920 px' }, { value: 0, label: 'Original' }]} />
        </Row>
        <Row label="GPU video decoding" hint="Auto uses it for videos longer than 5 minutes (it costs ~1 s to start)."><Seg value={c.hwDecode} onChange={set('hwDecode')} options={[{ value: 'auto', label: 'Auto' }, { value: true, label: 'On' }, { value: false, label: 'Off' }]} /></Row>
        <Row label="Parallel scans" hint="Clips scanned at the same time." wide>{num('workers', 1, 8)}</Row>
        <Row label="CPU threads per scan" hint="0 = all cores." wide>{num('threadsPerWorker', 0, 16)}</Row>
        <Row label="Low priority" hint="Scans yield CPU to games and other apps."><Toggle checked={c.lowPriority} onChange={set('lowPriority')} /></Row>
        <Row label="Pause scans while gaming" hint="Library scans wait until no game is running."><Toggle checked={c.pauseWhileGaming} onChange={set('pauseWhileGaming')} /></Row>
        <Row label="Skip clips already scanned" hint="Library scans only check clips a finder hasn't seen with its current settings."><Toggle checked={c.skipScanned} onChange={set('skipScanned')} /></Row>
      </Section>
      <Section title="Results">
        <Row label="Merge hits closer than" hint="Consecutive matching frames within this gap count as one match." wide>{num('minGapSec', 0.2, 30, 0.1, (v) => `${v.toFixed(1)}s`)}</Row>
        <Row label="Max matches per clip" wide>{num('maxResultsPerClip', 1, 500)}</Row>
        <Row label="Stop at the first match in each clip" hint="Fast “does this clip contain X?” checks."><Toggle checked={c.stopAfterFirst} onChange={set('stopAfterFirst')} /></Row>
        <Row label="Match thumbnails"><Toggle checked={c.thumbnails} onChange={set('thumbnails')} /></Row>
        <Row label="Seconds before a match" hint="Used for “Clip it” and montages." wide>{num('padBefore', 0, 30)}</Row>
        <Row label="Seconds after a match" wide>{num('padAfter', 0, 30)}</Row>
      </Section>
      <Section title="Automation">
        <Row label="Scan new clips automatically" hint="Every new clip is checked with finders that have Auto on."><Toggle checked={c.autoScanNewClips} onChange={set('autoScanNewClips')} /></Row>
        <Row label="Tag matching clips" hint="Adds the finder's tag (or name) to clips with a match."><Toggle checked={c.autoTag} onChange={set('autoTag')} /></Row>
        <Row label="Bookmark matches" hint="Adds a bookmark at each match — it shows on the editor timeline."><Toggle checked={c.addBookmarks} onChange={set('addBookmarks')} /></Row>
      </Section>
      <div className="dim" style={{ fontSize: 11.5, textAlign: 'center', marginBottom: 20 }}>Feature matching is based on FindFrame by Gunbard (MIT License).</div>
    </div>
  );
}
