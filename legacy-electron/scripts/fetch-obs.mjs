// Downloads the official OBS Studio Windows build into vendor/obs so it can be
// bundled with the installer (electron-builder extraResources → resources/obs).
//
//   npm run fetch-obs                 latest stable release
//   OBS_VERSION=31.1.2 npm run fetch-obs   a specific release tag
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const vendor = path.join(root, 'vendor');
const dest = path.join(vendor, 'obs');
const tag = process.env.OBS_VERSION;
const headers = { 'User-Agent': 'mettaloid-build', Accept: 'application/vnd.github+json' };
if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;

const api = tag
  ? `https://api.github.com/repos/obsproject/obs-studio/releases/tags/${tag}`
  : 'https://api.github.com/repos/obsproject/obs-studio/releases/latest';

const rel = await (await fetch(api, { headers })).json();
if (!rel.assets) {
  console.error('GitHub API error:', rel.message || rel);
  process.exit(1);
}
const asset = rel.assets.find((a) => /windows(-x64)?\.zip$/i.test(a.name) && !/pdb|arm64|beta|rc\d/i.test(a.name));
if (!asset) {
  console.error('No Windows x64 zip found in release', rel.tag_name, rel.assets.map((a) => a.name));
  process.exit(1);
}


// Remove parts of OBS Mettaloid never uses (browser source/CEF, capture-card
// plugins, debug symbols) where OBS allows it.
const UNUSED = ['obs-browser', 'aja', 'aja-output-ui', 'decklink', 'decklink-captions', 'decklink-output-ui', 'obs-vst'];
function prune(dir) {
  if (!fs.existsSync(dir)) return;
  // OBS 33+ ("core/" layout) refuses to load any plugin if a core module is
  // missing, so only debug symbols are stripped there.
  const coreLayout = fs.existsSync(path.join(dir, 'core'));
  for (const name of coreLayout ? [] : UNUSED) {
    for (const base of [path.join('obs-plugins', '64bit'), path.join('data', 'obs-plugins')]) {
      fs.rmSync(path.join(dir, base, name), { recursive: true, force: true });
      fs.rmSync(path.join(dir, base, `${name}.dll`), { force: true });
      fs.rmSync(path.join(dir, base, `${name}.pdb`), { force: true });
    }
  }
  // pre-33 layout keeps CEF next to the plugins
  const legacy = path.join(dir, 'obs-plugins', '64bit');
  if (fs.existsSync(legacy)) {
    for (const f of fs.readdirSync(legacy)) {
      if (/^(libcef|chrome_elf|cef|obs-browser|snapshot_blob|v8_context|icudtl|vk_swiftshader|libEGL|libGLESv2|d3dcompiler_47|vulkan-1)/i.test(f) || /\.pak$/i.test(f)) {
        fs.rmSync(path.join(legacy, f), { recursive: true, force: true });
      }
    }
    fs.rmSync(path.join(legacy, 'locales'), { recursive: true, force: true });
  }
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.pdb$/i.test(e.name)) fs.rmSync(p, { force: true });
    }
  };
  walk(dir);
}

const versionFile = path.join(dest, '.mt-obs-version');
if (fs.existsSync(versionFile) && fs.readFileSync(versionFile, 'utf8') === rel.tag_name) {
  prune(dest);
  console.log(`OBS ${rel.tag_name} already present in vendor/obs`);
  process.exit(0);
}

fs.mkdirSync(vendor, { recursive: true });
const zip = path.join(vendor, asset.name);
console.log(`Downloading ${asset.name} (${(asset.size / 1e6).toFixed(0)} MB) from OBS ${rel.tag_name}…`);
if (!(fs.existsSync(zip) && fs.statSync(zip).size === asset.size)) {
  const res = await fetch(asset.browser_download_url, { headers: { 'User-Agent': headers['User-Agent'] } });
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  fs.writeFileSync(zip, Buffer.from(await res.arrayBuffer()));
}

console.log('Extracting…');
const tmp = path.join(vendor, 'obs-extract');
fs.rmSync(tmp, { recursive: true, force: true });
fs.mkdirSync(tmp, { recursive: true });
// tar.exe ships with Windows 10 1803+ and understands zip files
// Use Windows' bsdtar explicitly (Git Bash's GNU tar misreads "C:" paths)
const winTar = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'tar.exe');
execFileSync(fs.existsSync(winTar) ? winTar : 'tar', ['-xf', zip, '-C', tmp], { stdio: 'inherit' });

// Find the folder that contains bin/64bit/obs64.exe
function findRoot(dir, depth = 0) {
  if (fs.existsSync(path.join(dir, 'bin', '64bit', 'obs64.exe'))) return dir;
  if (depth > 2) return null;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.isDirectory()) {
      const r = findRoot(path.join(dir, e.name), depth + 1);
      if (r) return r;
    }
  }
  return null;
}
const obsRoot = findRoot(tmp);
if (!obsRoot) throw new Error('obs64.exe not found in archive');
fs.rmSync(dest, { recursive: true, force: true });
fs.renameSync(obsRoot, dest);
fs.rmSync(tmp, { recursive: true, force: true });
fs.rmSync(zip, { force: true });
// Never ship a config folder from the archive (Mettaloid writes its own)
fs.rmSync(path.join(dest, 'config'), { recursive: true, force: true });
prune(dest);
fs.writeFileSync(versionFile, rel.tag_name);
console.log(`OBS ${rel.tag_name} ready in vendor/obs`);
