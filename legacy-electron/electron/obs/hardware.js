// Hardware / OBS install detection helpers.
const { execFile } = require('child_process');
const fs = require('fs');
const path = require('path');

function run(cmd, args, timeout = 15000) {
  return new Promise((resolve) => {
    execFile(cmd, args, { windowsHide: true, timeout, maxBuffer: 8 * 1024 * 1024 }, (err, stdout) => {
      resolve(err ? '' : String(stdout));
    });
  });
}

let gpuCache = null;
async function detectGpus() {
  if (gpuCache) return gpuCache;
  const out = await run('powershell.exe', [
    '-NoProfile', '-NonInteractive', '-Command',
    'Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }'
  ]);
  gpuCache = out.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  return gpuCache;
}

// Pick the best hardware encoder vendor for this machine.
async function detectEncoderVendor() {
  const gpus = (await detectGpus()).join(' | ').toLowerCase();
  if (gpus.includes('nvidia')) return 'nvenc';
  if (gpus.includes('amd') || gpus.includes('radeon')) return 'amf';
  if (gpus.includes('intel')) return 'qsv';
  return 'x264';
}

async function fileVersion(exe) {
  const out = await run('powershell.exe', [
    '-NoProfile', '-NonInteractive', '-Command',
    `(Get-Item -LiteralPath '${exe.replace(/'/g, "''")}').VersionInfo.ProductVersion`
  ]);
  return out.trim();
}

function isWritable(dir) {
  try {
    const probe = path.join(dir, `.mt-write-test-${process.pid}`);
    fs.writeFileSync(probe, 'x');
    fs.unlinkSync(probe);
    return true;
  } catch {
    return false;
  }
}

module.exports = { detectGpus, detectEncoderVendor, fileVersion, isWritable, run };
