import React, { useEffect, useRef, useState } from 'react';
import { useApp } from '../App';
import { invoke, mediaUrl, fmtDur, fmtSize, fmtDate } from '../api';
import { Icon, Modal } from '../components/ui';
import { BoxDrawOverlay, FindFrameDialog } from '../components/FindFrameTool';

export default function Viewer({ id }) {
  const { lib, go, toast, route } = useApp();
  const [drawing, setDrawing] = useState(false);
  const [ffBox, setFfBox] = useState(null);
  const startTool = () => {
    if (vid.current) vid.current.pause();
    setDrawing(true);
  };
  const clip = lib.clips.find((c) => c.id === id);
  const vid = useRef();
  const [title, setTitle] = useState('');
  const [tag, setTag] = useState('');
  const [confirmDel, setConfirmDel] = useState(false);

  useEffect(() => { if (clip) setTitle(clip.title); }, [clip && clip.id, clip && clip.title]);
  const idx = lib.clips.findIndex((c) => c.id === id);
  const prev = idx > 0 ? lib.clips[idx - 1] : null;
  const next = idx >= 0 && idx < lib.clips.length - 1 ? lib.clips[idx + 1] : null;

  useEffect(() => {
    const k = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key === 'ArrowUp' && prev) go('/clip/' + prev.id);
      if (e.key === 'ArrowDown' && next) go('/clip/' + next.id);
      if (e.key === 'e' && clip && clip.type !== 'screenshot') go('/editor/' + clip.id);
      if (e.key === 'f' && clip) invoke('library:update', clip.id, { favorite: !clip.favorite });
      if ((e.key === 'b' || e.key === 'B') && clip && clip.type !== 'screenshot') startTool();
      if (e.key === 'Escape' && !drawing && !ffBox) go('/library');
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [prev, next, clip, go, drawing, ffBox]);

  if (!clip) return <div className="content"><div className="empty">Clip not found. <button className="btn" onClick={() => go('/library')}>Back to library</button></div></div>;
  const isVideo = clip.type !== 'screenshot';
  const save = (patch) => invoke('library:update', clip.id, patch);
  const album = (aid) => lib.albums.find((a) => a.id === aid);

  return (
    <>
      <div className="topbar">
        <button className="btn ghost sm" onClick={() => go('/library')}><Icon name="back" size={15} /> Library</button>
        <h1 style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{clip.title}</h1>
        <span className="grow" />
        <button className="btn ghost sm icon" disabled={!prev} onClick={() => go('/clip/' + prev.id)} title="Newer (↑)"><Icon name="back" size={15} style={{ transform: 'rotate(90deg)' }} /></button>
        <button className="btn ghost sm icon" disabled={!next} onClick={() => go('/clip/' + next.id)} title="Older (↓)"><Icon name="chevron" size={15} style={{ transform: 'rotate(90deg)' }} /></button>
      </div>
      <div className="content flush">
        <div className="viewer">
          <div className="viewer-stage">
            {isVideo ? (
              <div style={{ position: 'relative', flex: 1, minHeight: 0, display: 'flex' }}>
                <video ref={vid} key={clip.id} src={mediaUrl(clip.path)} controls={!drawing} autoPlay={!route.query.get('t')}
                  onLoadedMetadata={(e) => { const t = Number(route.query.get('t')); if (t > 0) e.target.currentTime = t; }} />
                {drawing && <BoxDrawOverlay videoRef={vid} onCancel={() => setDrawing(false)} onBox={(b) => { setDrawing(false); setFfBox({ box: b, time: vid.current.currentTime }); }} />}
              </div>
            ) : <img src={mediaUrl(clip.path)} alt="" />}
            {isVideo && (
              <div className="row" style={{ padding: '8px 12px', background: 'var(--bg-2)', borderTop: '1px solid var(--line)' }}>
                <button className={`btn sm ${drawing ? 'primary' : ''}`} onClick={() => (drawing ? setDrawing(false) : startTool())} title="Pause and draw a box to find that frame/object elsewhere (B)">
                  <Icon name="target" size={14} /> {drawing ? 'Cancel' : 'Find Frame'}
                </button>
                <span className="dim" style={{ fontSize: 12 }}>Pause on a moment, draw a box around something (kill icon, banner, item…) and find every other time it appears.</span>
              </div>
            )}
            {clip.audioTracks && clip.audioTracks.length > 1 && (
              <div className="dim" style={{ padding: '6px 12px', fontSize: 12, background: 'var(--bg-2)' }}>
                This clip has {clip.audioTracks.length} audio tracks ({clip.audioTracks.map((t) => t.title).join(', ')}). Preview plays the mix; adjust tracks separately in the editor.
              </div>
            )}
          </div>
          <div className="viewer-side">
            <div className="col" style={{ gap: 8 }}>
              {isVideo && <button className="btn primary lg" onClick={() => go('/editor/' + clip.id)}><Icon name="editor" /> Edit clip</button>}
              <div className="row">
                {isVideo && <button className="btn grow" onClick={() => go('/compress/' + clip.id)}><Icon name="compress" size={15} /> Compress</button>}
                <button className="btn grow" onClick={async () => { const ok = await invoke('library:copyToClipboard', clip.id); toast({ title: ok ? 'Copied — paste into Discord or any chat' : 'Copy failed', kind: ok ? '' : 'err' }); }}><Icon name="copy" size={15} /> Copy file</button>
              </div>
              <div className="row">
                <button className={`btn grow ${clip.favorite ? 'primary' : ''}`} onClick={() => save({ favorite: !clip.favorite })}><Icon name="star" size={15} fill={clip.favorite ? 'currentColor' : 'none'} /> {clip.favorite ? 'Favorited' : 'Favorite'}</button>
                <button className="btn icon" title="Show in folder" onClick={() => invoke('app:showItem', clip.path)}><Icon name="folder" size={15} /></button>
                <button className="btn icon" title="Open in default player" onClick={() => invoke('app:openPath', clip.path)}><Icon name="external" size={15} /></button>
                <button className="btn icon danger" title="Delete" onClick={() => setConfirmDel(true)}><Icon name="trash" size={15} /></button>
              </div>
            </div>

            <div className="field">
              <label>Title</label>
              <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} onBlur={() => title !== clip.title && save({ title })} onKeyDown={(e) => e.key === 'Enter' && e.target.blur()} />
              <button className="btn sm ghost" style={{ alignSelf: 'flex-start' }} onClick={() => save({ title, renameFile: true })}>Rename file on disk too</button>
            </div>

            <div className="kv">
              <span>Game</span><span>{clip.game ? clip.game.name : 'Desktop'}</span>
              <span>Captured</span><span>{fmtDate(clip.createdAt)}</span>
              {isVideo && <><span>Duration</span><span>{fmtDur(clip.duration)}</span></>}
              <span>Resolution</span><span>{clip.width}×{clip.height}{clip.fps ? ` @ ${Math.round(clip.fps)} fps` : ''}</span>
              {clip.videoCodec && <><span>Codec</span><span>{clip.videoCodec.toUpperCase()}</span></>}
              <span>Size</span><span>{fmtSize(clip.size)}{clip.duration ? ` · ${Math.round((clip.size * 8) / clip.duration / 1000)} kbps` : ''}</span>
              {isVideo && <><span>Audio</span><span>{clip.audioTracks?.length || 0} track(s)</span></>}
              {clip.event && <><span>Event</span><span>{clip.event}</span></>}
              <span>File</span><span className="mono" style={{ fontSize: 11, wordBreak: 'break-all', userSelect: 'text' }}>{clip.path}</span>
            </div>

            <div className="field">
              <label>Tags</label>
              <div className="row wrap" style={{ gap: 6 }}>
                {(clip.tags || []).map((t) => <span className="tag" key={t}>#{t}<button onClick={() => save({ tags: clip.tags.filter((x) => x !== t) })}>×</button></span>)}
              </div>
              <input type="text" placeholder="Add tag and press Enter" value={tag} onChange={(e) => setTag(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && tag.trim()) { save({ tags: [...new Set([...(clip.tags || []), tag.trim().replace(/^#/, '')])] }); setTag(''); } }} />
            </div>

            <div className="field">
              <label>Albums</label>
              <div className="row wrap" style={{ gap: 6 }}>
                {(clip.albums || []).map((a) => album(a) && <span className="tag" key={a}>{album(a).name}<button onClick={() => invoke('albums:remove', a, [clip.id])}>×</button></span>)}
                <select value="" onChange={(e) => e.target.value && invoke('albums:add', e.target.value, [clip.id])} style={{ height: 26, padding: '0 8px', fontSize: 12 }}>
                  <option value="">+ Add to album</option>
                  {lib.albums.filter((a) => !(clip.albums || []).includes(a.id)).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </div>
            </div>

            {clip.bookmarks && clip.bookmarks.length > 0 && (
              <div className="field">
                <label>Bookmarks <em>{clip.bookmarks.length}</em></label>
                <div className="card">
                  {clip.bookmarks.map((b, i) => (
                    <div key={i} className="row" style={{ padding: '8px 10px', borderTop: i ? '1px solid var(--line)' : 0 }}>
                      <span style={{ color: '#c4b5fd' }}>★</span>
                      <button className="btn ghost sm" onClick={() => { if (vid.current) { vid.current.currentTime = b.time; vid.current.play(); } }}>{fmtDur(b.time)}</button>
                      <span className="grow muted" style={{ fontSize: 12 }}>{b.label}</span>
                      <button className="btn sm" onClick={() => go(`/editor/${clip.id}?start=${Math.max(0, b.time - 20)}&end=${Math.min(clip.duration, b.time + 10)}`)}>Clip it</button>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="dim" style={{ fontSize: 11.5 }}>Shortcuts: <kbd>E</kbd> edit · <kbd>F</kbd> favorite · <kbd>↑</kbd><kbd>↓</kbd> navigate</div>
          </div>
        </div>
      </div>
      {ffBox && <FindFrameDialog clip={clip} time={ffBox.time} box={ffBox.box} onClose={() => setFfBox(null)} />}
      {confirmDel && (
        <Modal title="Delete this clip?" onClose={() => setConfirmDel(false)}
          footer={<><button className="btn" onClick={() => setConfirmDel(false)}>Cancel</button><button className="btn" style={{ background: 'var(--red)', borderColor: 'var(--red)', color: '#fff' }} onClick={async () => { const n = next || prev; await invoke('library:remove', [clip.id]); setConfirmDel(false); go(n ? '/clip/' + n.id : '/library'); }}>Delete</button></>}>
          <div className="muted">{clip.external ? 'This file is outside your clip folder, so it will only be removed from the library.' : 'The file will be moved to the Recycle Bin.'}</div>
        </Modal>
      )}
    </>
  );
}
