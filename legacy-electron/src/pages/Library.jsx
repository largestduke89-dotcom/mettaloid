import React, { useMemo, useState } from 'react';
import { useApp } from '../App';
import { invoke, pathForFile } from '../api';
import { Icon, Modal, Select } from '../components/ui';
import ClipCard from '../components/ClipCard';

const VIEWS = [
  { id: 'all', label: 'All media', icon: 'library' },
  { id: 'clip', label: 'Clips', icon: 'clip' },
  { id: 'session', label: 'Sessions', icon: 'film' },
  { id: 'screenshot', label: 'Screenshots', icon: 'image' },
  { id: 'export', label: 'Edited & compressed', icon: 'editor' },
  { id: 'import', label: 'Imported', icon: 'download' },
  { id: 'fav', label: 'Favorites', icon: 'star' }
];

export default function Library() {
  const { lib, go, toast, route } = useApp();
  const [view, setView] = useState(route.query.get('view') || 'all');
  const [album, setAlbum] = useState(null);
  const [game, setGame] = useState('all');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState('new');
  const [size, setSize] = useState(250);
  const [sel, setSel] = useState(new Set());
  const [lastIdx, setLastIdx] = useState(null);
  const [albumModal, setAlbumModal] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [drag, setDrag] = useState(false);

  const games = useMemo(() => {
    const m = new Map();
    for (const c of lib.clips) if (c.game) m.set(c.game.name, (m.get(c.game.name) || 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [lib.clips]);

  const filtered = useMemo(() => {
    let list = lib.clips;
    if (album) list = list.filter((c) => (c.albums || []).includes(album));
    else if (view === 'fav') list = list.filter((c) => c.favorite);
    else if (view !== 'all') list = list.filter((c) => c.type === view);
    if (game !== 'all') list = list.filter((c) => (c.game ? c.game.name : 'Desktop') === game);
    if (q.trim()) {
      const s = q.toLowerCase();
      list = list.filter((c) => [c.title, c.game && c.game.name, c.event, ...(c.tags || []), c.path].filter(Boolean).some((x) => x.toLowerCase().includes(s)));
    }
    const sorters = {
      new: (a, b) => b.createdAt - a.createdAt,
      old: (a, b) => a.createdAt - b.createdAt,
      big: (a, b) => (b.size || 0) - (a.size || 0),
      long: (a, b) => (b.duration || 0) - (a.duration || 0),
      name: (a, b) => a.title.localeCompare(b.title)
    };
    return list.slice().sort(sorters[sort]);
  }, [lib.clips, view, album, game, q, sort]);

  const groups = useMemo(() => {
    if (sort !== 'new' && sort !== 'old') return [{ label: null, items: filtered }];
    const out = [];
    for (const c of filtered) {
      const d = new Date(c.createdAt).toDateString();
      const label = d === new Date().toDateString() ? 'Today' : d === new Date(Date.now() - 864e5).toDateString() ? 'Yesterday' : new Date(c.createdAt).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
      if (!out.length || out[out.length - 1].label !== label) out.push({ label, items: [] });
      out[out.length - 1].items.push(c);
    }
    return out;
  }, [filtered, sort]);

  const toggle = (c, e) => {
    const idx = filtered.indexOf(c);
    const n = new Set(sel);
    if (e && e.shiftKey && lastIdx !== null) {
      const [a, b] = [Math.min(lastIdx, idx), Math.max(lastIdx, idx)];
      for (let i = a; i <= b; i++) n.add(filtered[i].id);
    } else if (n.has(c.id)) n.delete(c.id);
    else n.add(c.id);
    setLastIdx(idx);
    setSel(n);
  };

  const ids = [...sel];
  const selClips = lib.clips.filter((c) => sel.has(c.id));
  const doDelete = async () => {
    await invoke('library:remove', ids, { deleteFiles: true });
    toast({ title: `Moved ${ids.length} item(s) to the Recycle Bin` });
    setSel(new Set());
    setConfirmDel(false);
  };
  const favorite = async () => {
    const val = !selClips.every((c) => c.favorite);
    for (const id of ids) await invoke('library:update', id, { favorite: val });
  };
  const montage = () => {
    const videos = selClips.filter((c) => c.type !== 'screenshot').sort((a, b) => a.createdAt - b.createdAt);
    if (videos.length < 1) return;
    go(`/editor/montage?ids=${videos.map((c) => c.id).join(',')}`);
  };

  const onDrop = async (e) => {
    e.preventDefault();
    setDrag(false);
    const files = [...e.dataTransfer.files].map((f) => pathForFile(f)).filter(Boolean);
    if (!files.length) return;
    const added = await invoke('library:import', files, { copy: false });
    toast({ title: `Imported ${added.length} file(s)` });
  };

  return (
    <div className="content flush" onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}>
      <div className="lib">
        <div className="lib-side">
          {VIEWS.map((v) => (
            <button key={v.id} className={`nav-item ${!album && view === v.id ? 'active' : ''}`} onClick={() => { setView(v.id); setAlbum(null); }}>
              <Icon name={v.icon} size={16} /> {v.label}
              <span className="count">{v.id === 'all' ? lib.clips.length : v.id === 'fav' ? lib.clips.filter((c) => c.favorite).length : lib.clips.filter((c) => c.type === v.id).length || ''}</span>
            </button>
          ))}
          <div className="row" style={{ paddingRight: 6 }}>
            <div className="h3 grow">Albums</div>
            <button className="btn ghost sm icon" title="New album" onClick={async () => { const a = await invoke('albums:create', 'New album'); setAlbum(a.id); }}><Icon name="plus" size={14} /></button>
          </div>
          {lib.albums.map((a) => (
            <AlbumItem key={a.id} album={a} active={album === a.id} count={lib.clips.filter((c) => (c.albums || []).includes(a.id)).length} onClick={() => setAlbum(a.id)} />
          ))}
          {lib.albums.length === 0 && <div className="dim" style={{ padding: '2px 11px', fontSize: 12 }}>No albums yet</div>}
          {games.length > 0 && <div className="h3">Games</div>}
          {games.map(([name, n]) => (
            <button key={name} className={`nav-item ${game === name ? 'active' : ''}`} onClick={() => setGame(game === name ? 'all' : name)}>
              <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span><span className="count">{n}</span>
            </button>
          ))}
        </div>
        <div className="lib-main" style={{ position: 'relative' }}>
          <div className="lib-toolbar" style={{ paddingRight: 150 }}>
            <div className="row grow" style={{ maxWidth: 380, minWidth: 260, position: 'relative' }}>
              <Icon name="search" size={15} style={{ position: 'absolute', left: 10, color: 'var(--text-3)' }} />
              <input type="search" placeholder="Search clips, games, tags…" value={q} onChange={(e) => setQ(e.target.value)} style={{ paddingLeft: 32, width: '100%' }} />
            </div>
            <Select value={game} onChange={setGame} options={[{ value: 'all', label: 'All games' }, ...games.map(([g]) => ({ value: g, label: g })), { value: 'Desktop', label: 'Desktop' }]} />
            <Select value={sort} onChange={setSort} options={[{ value: 'new', label: 'Newest' }, { value: 'old', label: 'Oldest' }, { value: 'big', label: 'Largest' }, { value: 'long', label: 'Longest' }, { value: 'name', label: 'Name' }]} />
            <div className="spacer" />
            <button className="btn ghost sm icon" title="Smaller" onClick={() => setSize(Math.max(170, size - 40))}><Icon name="zoomOut" size={15} /></button>
            <button className="btn ghost sm icon" title="Larger" onClick={() => setSize(Math.min(450, size + 40))}><Icon name="zoomIn" size={15} /></button>
            <button className="btn sm" onClick={() => invoke('library:import').then((a) => a.length && toast({ title: `Imported ${a.length} file(s)` }))}><Icon name="download" size={14} /> Import</button>
            <button className="btn sm" onClick={() => setSel(sel.size ? new Set() : new Set(filtered.map((c) => c.id)))}>{sel.size ? 'Deselect' : 'Select all'}</button>
          </div>
          <div className={`lib-grid-wrap ${sel.size ? 'selecting' : ''}`} style={{ outline: drag ? '2px dashed var(--accent)' : 'none', outlineOffset: -8 }}>
            {album && <AlbumHeader key={album} album={lib.albums.find((a) => a.id === album)} onDeleted={() => setAlbum(null)} />}
            {filtered.length === 0 && (
              <div className="empty"><div><div className="big">📂</div><b>Nothing here</b><div className="dim" style={{ marginTop: 4 }}>Drag & drop videos here to import them.</div></div></div>
            )}
            {groups.map((g, gi) => (
              <div key={gi}>
                {g.label && <div className="day-head">{g.label}</div>}
                <div className="grid" style={{ '--card-w': `${size}px` }}>
                  {g.items.map((c) => (
                    <ClipCard key={c.id} clip={c} selected={sel.has(c.id)} selecting={sel.size > 0} onClick={() => go('/clip/' + c.id)} onToggleSelect={(e) => toggle(c, e)} />
                  ))}
                </div>
              </div>
            ))}
          </div>
          {sel.size > 0 && (
            <div className="bulk-bar">
              <b style={{ marginRight: 6 }}>{sel.size} selected</b>
              <button className="btn sm" onClick={favorite}><Icon name="star" size={14} /> Favorite</button>
              <button className="btn sm" onClick={() => setAlbumModal(true)}><Icon name="album" size={14} /> Album</button>
              <button className="btn sm" onClick={montage} disabled={!selClips.some((c) => c.type !== 'screenshot')}><Icon name="merge" size={14} /> {selClips.length > 1 ? 'Montage' : 'Edit'}</button>
              <button className="btn sm" onClick={() => go(`/compressor?ids=${ids.join(',')}`)} disabled={!selClips.some((c) => c.type !== 'screenshot')}><Icon name="compress" size={14} /> Compress</button>
              {album && <button className="btn sm" onClick={() => invoke('albums:remove', album, ids)}>Remove from album</button>}
              <button className="btn sm danger" onClick={() => setConfirmDel(true)}><Icon name="trash" size={14} /> Delete</button>
              <button className="btn sm ghost icon" onClick={() => setSel(new Set())}><Icon name="x" size={14} /></button>
            </div>
          )}
        </div>
      </div>
      {albumModal && (
        <Modal title="Add to album" onClose={() => setAlbumModal(false)}>
          <div className="col">
            {lib.albums.map((a) => (
              <button key={a.id} className="btn" style={{ justifyContent: 'flex-start' }} onClick={async () => { await invoke('albums:add', a.id, ids); setAlbumModal(false); toast({ title: `Added to ${a.name}` }); }}>
                <Icon name="album" size={15} /> {a.name}
              </button>
            ))}
            <NewAlbum onCreate={async (name) => { const a = await invoke('albums:create', name); await invoke('albums:add', a.id, ids); setAlbumModal(false); toast({ title: `Added to ${name}` }); }} />
          </div>
        </Modal>
      )}
      {confirmDel && (
        <Modal title={`Delete ${ids.length} item(s)?`} onClose={() => setConfirmDel(false)}
          footer={<><button className="btn" onClick={() => setConfirmDel(false)}>Cancel</button><button className="btn primary" style={{ background: 'var(--red)', borderColor: 'var(--red)', color: '#fff' }} onClick={doDelete}>Delete</button></>}>
          <div className="muted">Files in your clip folder are moved to the Recycle Bin. Imported files outside the clip folder are only removed from the library.</div>
          {selClips.some((c) => c.favorite) && <div style={{ marginTop: 10, color: 'var(--accent-2)' }}>⚠ {selClips.filter((c) => c.favorite).length} of these are favorites.</div>}
        </Modal>
      )}
    </div>
  );
}

function NewAlbum({ onCreate }) {
  const [name, setName] = useState('');
  return (
    <div className="row" style={{ marginTop: 6 }}>
      <input type="text" className="grow" placeholder="New album name" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && name && onCreate(name)} />
      <button className="btn primary" disabled={!name} onClick={() => onCreate(name)}>Create</button>
    </div>
  );
}

function AlbumItem({ album, active, count, onClick }) {
  return (
    <button className={`nav-item ${active ? 'active' : ''}`} onClick={onClick}>
      <Icon name="album" size={16} /><span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{album.name}</span><span className="count">{count || ''}</span>
    </button>
  );
}

function AlbumHeader({ album, onDeleted }) {
  const [name, setName] = useState(album ? album.name : '');
  if (!album) return null;
  return (
    <div className="row" style={{ marginBottom: 16 }}>
      <input type="text" value={name} onChange={(e) => setName(e.target.value)} onBlur={() => invoke('albums:rename', album.id, name)} style={{ fontSize: 18, fontWeight: 700, background: 'transparent', border: '1px solid transparent' }} />
      <span className="grow" />
      <button className="btn sm danger" onClick={async () => { await invoke('albums:delete', album.id); onDeleted(); }}><Icon name="trash" size={14} /> Delete album</button>
    </div>
  );
}
