// Local auto-clip API (127.0.0.1 only). Lets games, mods, scripts, Stream Deck
// buttons, etc. trigger clips on in-game events — the same idea as Medal's
// game-event integrations, without needing per-game memory reading.
//
//   POST /clip      {"event":"Ace","before":15,"after":5}
//   POST /bookmark  {"label":"Clutch"}
//   GET  /status
const http = require('http');

function startApiServer({ settings, onClip, onBookmark, getStatus }) {
  let server = null;
  const listen = () => {
    if (server) { server.close(); server = null; }
    const cfg = settings.get().autoClip;
    if (!cfg.enabled) return;
    server = http.createServer((req, res) => {
      const reply = (code, body) => {
        res.writeHead(code, { 'content-type': 'application/json' });
        res.end(JSON.stringify(body));
      };
      // Block browsers on other origins from poking the API.
      if (req.headers.origin) return reply(403, { error: 'forbidden' });
      let raw = '';
      req.on('data', (d) => { raw += d; if (raw.length > 1e5) req.destroy(); });
      req.on('end', async () => {
        let body = {};
        try { body = raw ? JSON.parse(raw) : {}; } catch { return reply(400, { error: 'invalid json' }); }
        try {
          if (req.method === 'GET' && req.url === '/status') return reply(200, getStatus());
          if (req.method === 'POST' && req.url === '/clip') {
            const r = await onClip({
              event: String(body.event || 'Event').slice(0, 60),
              before: Math.min(600, Math.max(1, Number(body.before) || cfg.secondsBefore)),
              after: Math.min(60, Math.max(0, Number(body.after ?? cfg.secondsAfter)))
            });
            return reply(200, r);
          }
          if (req.method === 'POST' && req.url === '/bookmark') return reply(200, await onBookmark(String(body.label || 'Bookmark').slice(0, 60)));
          reply(404, { error: 'not found' });
        } catch (e) {
          reply(500, { error: e.message });
        }
      });
    });
    server.on('error', () => {});
    server.listen(cfg.apiPort, '127.0.0.1');
  };
  listen();
  return { restart: listen, close: () => server && server.close() };
}

module.exports = { startApiServer };
