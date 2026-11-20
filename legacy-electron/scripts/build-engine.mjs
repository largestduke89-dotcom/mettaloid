// Builds the Find Frame engine (Python + OpenCV) into a standalone folder
// findframe-engine/dist/findframe-engine/findframe-engine.exe, which
// electron-builder bundles as resources/findframe.
//
//   npm run build:engine
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'findframe-engine');
const venv = path.join(root, '.venv');
const py = path.join(venv, 'Scripts', 'python.exe');
const run = (cmd, args) => execFileSync(cmd, args, { cwd: root, stdio: 'inherit' });

if (!fs.existsSync(py)) {
  console.log('Creating Python virtual environment…');
  run(process.env.PYTHON || 'python', ['-m', 'venv', venv]);
}
run(py, ['-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', 'requirements.txt']);
run(py, ['-m', 'PyInstaller', '--noconfirm', '--onedir', '--console', '--name', 'findframe-engine',
  '--distpath', 'dist', '--workpath', 'build', '--specpath', 'build',
  '--exclude-module', 'tkinter', '--exclude-module', 'ssl', '--exclude-module', '_ssl',
  '--exclude-module', 'matplotlib', '--exclude-module', 'unittest', 'engine.py']);

// The engine decodes frames through Mettaloid's FFmpeg, so OpenCV's own
// bundled FFmpeg (≈40 MB) and OpenSSL are dead weight.
const internal = path.join(root, 'dist', 'findframe-engine', '_internal');
const prune = (dir) => {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) prune(p);
    else if (/^opencv_videoio_ffmpeg.*\.dll$/i.test(e.name) || /^lib(crypto|ssl)-\d.*\.dll$/i.test(e.name)) fs.rmSync(p);
  }
};
prune(internal);
const out = execFileSync(path.join(root, 'dist', 'findframe-engine', 'findframe-engine.exe'), ['--version']).toString();
console.log('Engine ready:', out.trim());
