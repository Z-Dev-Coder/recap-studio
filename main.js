/* ------------------------------------------------------------------
   Toolbox - one Electron app hosting several tool modules.

   Modules live in modules/ and are declared in modules/manifest.json:
     type "page"        -> an HTML page loaded in the module frame
     type "service-web" -> a local process is started and its URL is loaded

   Adding a tool later means dropping a folder in modules/ and adding
   a manifest entry. No changes to this file are needed for either kind.
------------------------------------------------------------------ */
const { app, BrowserWindow, ipcMain, desktopCapturer, screen, shell,
        globalShortcut, dialog, Tray, Menu, nativeImage, session } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const { spawn, execFile } = require('child_process');

// Without this, a dev run resolves userData to ...\Roaming\Electron while the
// packaged app uses ...\Roaming\Toolbox -- so the two would keep separate
// settings and, worse, separate service environments.
app.setName('Toolbox');

const SETTINGS_FILE = () => path.join(app.getPath('userData'), 'settings.json');
const MANIFEST_FILE = path.join(__dirname, 'modules', 'manifest.json');

let DEFAULTS = null;
let settings = {};
let manifest = { modules: [], services: {} };
let mainWindow = null;
let overlayWindow = null;
let panelWindow = null;
let camWindow = null;
let tray = null;

/* ================= settings ================= */
function buildDefaults() {
  return {
    outputDir: path.join(app.getPath('videos'), 'ScreenRecorder'),
    format: 'mp4',
    quality: 'high',
    fps: 30,
    countdown: 3,
    micEnabled: true,
    systemAudio: true,
    webcamEnabled: false,
    webcamPosition: 'bottom-right',
    webcamSize: 220,
    // last place the floating camera bubble was dragged to (null = default corner)
    camBubble: null,
    hotkeys: { startStop: 'F9', pause: 'F10', screenshot: 'F11', camera: 'F8' },
    minimizeOnRecord: true,
    autoStopMinutes: 0,
    lastModule: 'dashboard'
  };
}

function loadSettings() {
  DEFAULTS = buildDefaults();
  try {
    const raw = JSON.parse(fs.readFileSync(SETTINGS_FILE(), 'utf8'));
    settings = { ...DEFAULTS, ...raw, hotkeys: { ...DEFAULTS.hotkeys, ...(raw.hotkeys || {}) } };
  } catch {
    settings = { ...DEFAULTS };
  }
  fs.mkdirSync(settings.outputDir, { recursive: true });
  return settings;
}

function saveSettings(next) {
  settings = { ...settings, ...next, hotkeys: { ...settings.hotkeys, ...(next.hotkeys || {}) } };
  fs.mkdirSync(settings.outputDir, { recursive: true });
  fs.writeFileSync(SETTINGS_FILE(), JSON.stringify(settings, null, 2));
  registerHotkeys();
  return settings;
}

function loadManifest() {
  try {
    manifest = JSON.parse(fs.readFileSync(MANIFEST_FILE, 'utf8'));
  } catch (e) {
    manifest = { modules: [], services: {} };
  }
  manifest.modules = (manifest.modules || []).filter(m => m.enabled !== false);
  return manifest;
}

/* ================= local services ================= */
const services = {};   // id -> { proc, url, port, status, error }

function freePort() {
  return new Promise((resolve, reject) => {
    const s = http.createServer();
    s.on('error', reject);
    s.listen(0, '127.0.0.1', () => {
      const p = s.address().port;
      s.close(() => resolve(p));
    });
  });
}

function ping(url) {
  return new Promise(resolve => {
    const req = http.get(url, res => { res.resume(); resolve(res.statusCode < 500); });
    req.on('error', () => resolve(false));
    req.setTimeout(1500, () => { req.destroy(); resolve(false); });
  });
}

/* Where the service's Python lives.

   The bundled venv sits inside the app folder, and an installer wipes that
   folder before writing the new version -- so anything installed into it
   (PyTorch and VoxCPM run to several gigabytes) is destroyed on every update,
   and the delete itself makes the install crawl.

   A venv in userData survives updates and is checked first. The bundled one
   remains the fallback, so a fresh install still works with no setup. */
function externalVenv() {
  return path.join(app.getPath('userData'), 'service', 'Scripts', 'python.exe');
}

function pythonFor(root, cfg) {
  const outside = externalVenv();
  if (fs.existsSync(outside)) return outside;
  return path.isAbsolute(cfg.python) ? cfg.python : path.join(root, cfg.python);
}

async function startService(id) {
  const cfg = (manifest.services || {})[id];
  if (!cfg) throw new Error('Unknown service: ' + id);

  const running = services[id];
  if (running && running.proc && running.proc.exitCode === null && running.status === 'ready') {
    return { url: running.url, status: 'ready' };
  }

  // a relative projectRoot is resolved against the app folder, so a service
  // bundled under services/ keeps working wherever Toolbox is installed
  const root = path.isAbsolute(cfg.projectRoot)
    ? cfg.projectRoot
    : path.join(__dirname, cfg.projectRoot);
  const exe = pythonFor(root, cfg);
  if (!fs.existsSync(exe)) {
    throw new Error('Interpreter not found:\n' + exe + '\n\n' + (cfg.setupHint || ''));
  }

  const port = await freePort();
  const args = (cfg.args || []).map(a => String(a).replace('{port}', String(port)));
  const env = { ...process.env, PYTHONPATH: root, PYTHONUNBUFFERED: '1' };
  delete env.ELECTRON_RUN_AS_NODE;

  const proc = spawn(exe, args, { cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const rec = { proc, port, url: 'http://127.0.0.1:' + port, status: 'starting', log: '' };
  services[id] = rec;

  const capture = d => { rec.log = (rec.log + d.toString()).slice(-4000); };
  proc.stdout.on('data', capture);
  proc.stderr.on('data', capture);
  proc.on('exit', code => {
    rec.status = 'stopped';
    rec.exitCode = code;
    send('service:state', { id, status: 'stopped', code });
  });

  const health = rec.url + (cfg.healthPath || '/');
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (await ping(health)) {
      rec.status = 'ready';
      send('service:state', { id, status: 'ready', url: rec.url });
      return { url: rec.url, status: 'ready' };
    }
    if (proc.exitCode !== null) break;
    await new Promise(r => setTimeout(r, 250));
  }
  stopService(id);
  throw new Error('Service did not start in time.\n\n' + rec.log.slice(-600));
}

function stopService(id) {
  const rec = services[id];
  if (!rec || !rec.proc || rec.proc.exitCode !== null) return;
  try {
    spawn('taskkill', ['/pid', String(rec.proc.pid), '/T', '/F'], { windowsHide: true });
  } catch {
    try { rec.proc.kill(); } catch {}
  }
}
function stopAllServices() { Object.keys(services).forEach(stopService); }

/* ================= ffmpeg ================= */
function ffmpegPath() {
  if (process.env.FFMPEG_PATH && fs.existsSync(process.env.FFMPEG_PATH)) return process.env.FFMPEG_PATH;
  return 'ffmpeg';
}
function hasFfmpeg() {
  return new Promise(res => execFile(ffmpegPath(), ['-version'], err => res(!err)));
}
const CRF = { low: 32, medium: 26, high: 20, lossless: 14 };

function convert(input, output, opts, onProgress) {
  return new Promise((resolve, reject) => {
    let args;
    if (opts.format === 'gif') {
      const filter = 'fps=' + Math.min(opts.fps, 15) +
        ',scale=720:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse';
      args = ['-y', '-i', input, '-vf', filter, output];
    } else if (opts.audioOnly) {
      args = ['-y', '-i', input, '-vn', '-c:a', 'aac', '-b:a', '192k', output];
    } else {
      args = ['-y', '-i', input,
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', String(CRF[opts.quality] || 20),
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
        '-c:a', 'aac', '-b:a', '192k', output];
    }
    const proc = spawn(ffmpegPath(), args);
    let log = '';
    proc.stderr.on('data', d => {
      const s = d.toString();
      log += s;
      const m = s.match(/time=(\d+):(\d+):(\d+\.\d+)/);
      if (m && onProgress) onProgress((+m[1]) * 3600 + (+m[2]) * 60 + parseFloat(m[3]));
    });
    proc.on('error', reject);
    proc.on('close', code => code === 0 ? resolve(output) : reject(new Error('ffmpeg failed: ' + log.slice(-800))));
  });
}

/* ================= windows ================= */
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1180, height: 780, minWidth: 960, minHeight: 640,
    backgroundColor: '#0f1115', show: false, autoHideMenuBar: true,
    title: 'Toolbox',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInSubFrames: true,   // module frames get the same preload bridge
      webviewTag: false
    }
  });
  mainWindow.loadFile(path.join(__dirname, 'renderer', 'shell.html'));
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('closed', () => { mainWindow = null; });
}

function virtualBounds() {
  const all = screen.getAllDisplays();
  const x = Math.min(...all.map(d => d.bounds.x));
  const y = Math.min(...all.map(d => d.bounds.y));
  return {
    x, y,
    width: Math.max(...all.map(d => d.bounds.x + d.bounds.width)) - x,
    height: Math.max(...all.map(d => d.bounds.y + d.bounds.height)) - y
  };
}

function createOverlay(mode) {
  if (overlayWindow) { overlayWindow.destroy(); overlayWindow = null; }
  const vb = virtualBounds();
  overlayWindow = new BrowserWindow({
    x: vb.x, y: vb.y, width: vb.width, height: vb.height,
    frame: false, transparent: true, alwaysOnTop: true, skipTaskbar: true,
    resizable: false, movable: false, hasShadow: false, enableLargerThanScreen: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true }
  });
  overlayWindow.setAlwaysOnTop(true, 'screen-saver');
  overlayWindow.loadFile(path.join(__dirname, 'renderer', 'overlay.html'), { query: { mode: mode || 'record' } });
  overlayWindow.on('closed', () => { overlayWindow = null; });
  return overlayWindow;
}

function createPanel() {
  if (panelWindow) return panelWindow;
  const wa = screen.getPrimaryDisplay().workArea;
  panelWindow = new BrowserWindow({
    width: 360, height: 70, x: wa.x + wa.width - 390, y: wa.y + wa.height - 110,
    frame: false, transparent: true, alwaysOnTop: true, skipTaskbar: true,
    resizable: false, hasShadow: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true }
  });
  panelWindow.setAlwaysOnTop(true, 'screen-saver');
  panelWindow.loadFile(path.join(__dirname, 'renderer', 'panel.html'));
  panelWindow.on('closed', () => { panelWindow = null; });
  return panelWindow;
}

/* The floating camera bubble: what you actually see of yourself while
   recording, and the thing you drag to place your face on screen.

   It is content-protected so the screen capture does not pick it up -- the
   recorder composites the camera into the video itself, at whatever position
   and size this window has been dragged to. Without that the face would be
   burned in twice, once sharp and once through the capture. */
function createCamWindow() {
  if (camWindow && !camWindow.isDestroyed()) return camWindow;

  const wa = screen.getPrimaryDisplay().workArea;
  const saved = settings.camBubble;
  const size = Math.max(140, Math.min(520, Number(settings.webcamSize) || 220));
  const b = saved && Number.isFinite(saved.x)
    ? saved
    : { x: wa.x + wa.width - size - 32, y: wa.y + wa.height - size - 120, width: size, height: size };

  camWindow = new BrowserWindow({
    x: Math.round(b.x), y: Math.round(b.y),
    width: Math.round(b.width), height: Math.round(b.height),
    minWidth: 120, minHeight: 120, maxWidth: 720, maxHeight: 720,
    frame: false, transparent: true, alwaysOnTop: true, skipTaskbar: true,
    resizable: true, hasShadow: false, show: false, fullscreenable: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true }
  });
  camWindow.setAlwaysOnTop(true, 'screen-saver');
  camWindow.setVisibleOnAllWorkspaces(true);
  camWindow.setAspectRatio(1);
  try { camWindow.setContentProtection(true); } catch {}
  camWindow.loadFile(path.join(__dirname, 'renderer', 'cam.html'));

  // 'move'/'resize' fire continuously (and for programmatic changes), so the
  // composited face tracks the bubble while it is being dragged rather than
  // jumping when the mouse is released. The position is only written to disk
  // once the dragging settles -- otherwise this is a file write per pixel.
  let persist = null;
  const remember = () => {
    if (!camWindow || camWindow.isDestroyed()) return;
    settings.camBubble = camWindow.getBounds();
    sendCamState();
    clearTimeout(persist);
    persist = setTimeout(() => {
      try { fs.writeFileSync(SETTINGS_FILE(), JSON.stringify(settings, null, 2)); } catch {}
    }, 700);
  };
  camWindow.on('move', remember);
  camWindow.on('resize', remember);
  camWindow.on('closed', () => { camWindow = null; sendCamState(); });
  return camWindow;
}

/* the recorder needs the bubble's live geometry to composite the face in the
   same spot the user sees it */
function camState() {
  const live = camWindow && !camWindow.isDestroyed();
  return {
    open: !!live,
    visible: !!(live && camWindow.isVisible()),
    bounds: live ? camWindow.getBounds() : null
  };
}
function sendCamState() {
  // the floating panel needs this as much as the recorder does
  const st = camState();
  [mainWindow, panelWindow].forEach(w => {
    if (w && !w.isDestroyed()) w.webContents.send('cam:state', st);
  });
}

/* ================= hotkeys ================= */
function send(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
}

function registerHotkeys() {
  globalShortcut.unregisterAll();
  const hk = (settings && settings.hotkeys) || { startStop: 'F9', pause: 'F10', screenshot: 'F11', camera: 'F8' };
  const bind = (accel, fn) => { try { if (accel) globalShortcut.register(accel, fn); } catch {} };
  bind(hk.startStop, () => send('hotkey', 'startStop'));
  bind(hk.pause, () => send('hotkey', 'pause'));
  bind(hk.screenshot, () => send('hotkey', 'screenshot'));
  // hiding the face mid-take must work with nothing on screen to click
  bind(hk.camera, () => {
    if (!camWindow || camWindow.isDestroyed()) return;
    if (camWindow.isVisible()) camWindow.hide(); else camWindow.showInactive();
    sendCamState();
  });
}

/* ================= library ================= */
const MEDIA_EXT = ['.mp4', '.webm', '.gif', '.m4a', '.mp3', '.mkv', '.png', '.jpg'];

function listDir(dir, limit) {
  try {
    const items = fs.readdirSync(dir)
      .filter(f => MEDIA_EXT.includes(path.extname(f).toLowerCase()))
      .map(f => {
        const full = path.join(dir, f);
        const st = fs.statSync(full);
        return { name: f, path: full, size: st.size, mtime: st.mtimeMs, ext: path.extname(f).toLowerCase() };
      })
      .sort((a, b) => b.mtime - a.mtime);
    return limit ? items.slice(0, limit) : items;
  } catch { return []; }
}

function downloadsDir() {
  return process.env.YTDL_OUT || app.getPath('downloads');
}

/* ================= ipc ================= */
/* The page shows this so the install command names a real path rather than a
   guess the user has to translate. */
ipcMain.handle('app:servicePython', () => {
  const outside = externalVenv();
  return {
    external: outside,
    inUse: fs.existsSync(outside) ? outside : 'bundled',
    updateSafe: fs.existsSync(outside),
  };
});

ipcMain.handle('app:modules', () => loadManifest().modules);
ipcMain.handle('app:version', () => ({
  app: app.getVersion(), electron: process.versions.electron, node: process.versions.node
}));

ipcMain.handle('service:start', async (_e, id) => {
  try { return { ok: true, ...(await startService(id)) }; }
  catch (e) { return { ok: false, error: e.message }; }
});
ipcMain.handle('service:stop', (_e, id) => { stopService(id); return true; });
ipcMain.handle('service:status', (_e, id) => {
  const r = services[id];
  return r ? { status: r.status, url: r.url } : { status: 'stopped' };
});

ipcMain.handle('settings:get', () => settings);
ipcMain.handle('settings:set', (_e, next) => saveSettings(next));
ipcMain.handle('settings:pickDir', async () => {
  const r = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory', 'createDirectory'] });
  if (r.canceled || !r.filePaths[0]) return settings.outputDir;
  return saveSettings({ outputDir: r.filePaths[0] }).outputDir;
});

ipcMain.handle('sources:list', async () => {
  const sources = await desktopCapturer.getSources({
    types: ['screen', 'window'], thumbnailSize: { width: 320, height: 200 }, fetchWindowIcons: false
  });
  return sources
    .filter(s => s.name !== 'Toolbox')
    .map(s => ({
      id: s.id, name: s.name,
      type: s.id.startsWith('screen') ? 'screen' : 'window',
      display_id: s.display_id,
      thumbnail: s.thumbnail && !s.thumbnail.isEmpty() ? s.thumbnail.toDataURL() : null
    }));
});

ipcMain.handle('displays:get', () => {
  const vb = virtualBounds();
  const primary = screen.getPrimaryDisplay().id;
  return {
    origin: { x: vb.x, y: vb.y },
    displays: screen.getAllDisplays().map(d => ({
      id: d.id, bounds: d.bounds, scaleFactor: d.scaleFactor, primary: d.id === primary
    }))
  };
});

let regionResolver = null;
ipcMain.handle('region:select', (_e, mode) => new Promise(resolve => {
  regionResolver = resolve;
  if (mainWindow && mainWindow.isVisible()) mainWindow.hide();
  setTimeout(() => createOverlay(mode), 150);
}));
ipcMain.on('region:result', (_e, rect) => {
  if (overlayWindow) { overlayWindow.destroy(); overlayWindow = null; }
  if (mainWindow) mainWindow.show();
  if (regionResolver) { regionResolver(rect); regionResolver = null; }
});

ipcMain.on('window:hide', () => { if (mainWindow) mainWindow.minimize(); });
ipcMain.on('window:show', () => { if (mainWindow) { mainWindow.restore(); mainWindow.show(); mainWindow.focus(); } });

ipcMain.on('panel:open', (_e, state) => {
  const p = createPanel();
  p.show();
  p.webContents.once('did-finish-load', () => p.webContents.send('panel:state', state));
  p.webContents.send('panel:state', state);
});
ipcMain.on('panel:update', (_e, state) => {
  if (panelWindow && !panelWindow.isDestroyed()) panelWindow.webContents.send('panel:state', state);
});
ipcMain.on('panel:close', () => { if (panelWindow) { panelWindow.destroy(); panelWindow = null; } });
ipcMain.on('panel:action', (_e, action) => send('panel:action', action));

/* ---- floating camera bubble ---- */
ipcMain.handle('cam:open', (_e, opts) => {
  const w = createCamWindow();
  if (opts && opts.deviceId !== undefined) w.webContents.send('cam:device', opts.deviceId);
  w.webContents.once('did-finish-load', () => {
    w.webContents.send('cam:device', (opts && opts.deviceId) || '');
    w.showInactive();
    sendCamState();
  });
  if (!w.webContents.isLoading()) { w.showInactive(); }
  sendCamState();
  return camState();
});
ipcMain.handle('cam:close', () => {
  if (camWindow && !camWindow.isDestroyed()) { camWindow.destroy(); camWindow = null; }
  sendCamState();
  return camState();
});
ipcMain.handle('cam:setVisible', (_e, visible) => {
  if (camWindow && !camWindow.isDestroyed()) {
    if (visible) camWindow.showInactive(); else camWindow.hide();
  }
  sendCamState();
  return camState();
});
ipcMain.handle('cam:state', () => camState());

ipcMain.handle('recording:save', async (_e, payload) => {
  const { buffer, ext, audioOnly, baseName } = payload;
  fs.mkdirSync(settings.outputDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const base = (baseName || (audioOnly ? 'Audio' : 'Recording')) + '_' + stamp;
  const tmp = path.join(os.tmpdir(), base + '.' + ext);
  fs.writeFileSync(tmp, Buffer.from(buffer));

  const target = audioOnly ? 'm4a' : settings.format;
  const keepRaw = () => {
    const out = path.join(settings.outputDir, base + '.' + ext);
    fs.copyFileSync(tmp, out);
    fs.unlinkSync(tmp);
    return out;
  };

  if (target === 'webm' && !audioOnly) return { path: keepRaw() };
  if (!(await hasFfmpeg())) return { path: keepRaw(), warning: 'ffmpeg not found - saved raw ' + ext.toUpperCase() };

  const out = path.join(settings.outputDir, base + '.' + target);
  try {
    await convert(tmp, out, {
      format: audioOnly ? 'mp4' : target,
      audioOnly: !!audioOnly, quality: settings.quality, fps: settings.fps
    }, sec => send('convert:progress', sec));
    return { path: out };
  } catch (err) {
    return { path: keepRaw(), warning: 'Conversion failed, kept raw file. ' + err.message };
  } finally {
    try { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); } catch {}
  }
});

ipcMain.handle('screenshot:save', async (_e, { buffer }) => {
  fs.mkdirSync(settings.outputDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const out = path.join(settings.outputDir, 'Screenshot_' + stamp + '.png');
  fs.writeFileSync(out, Buffer.from(buffer));
  return { path: out };
});

ipcMain.handle('library:list', () => listDir(settings.outputDir));
ipcMain.handle('library:recent', (_e, limit) => ({
  recordings: listDir(settings.outputDir, limit || 6),
  downloads: listDir(downloadsDir(), limit || 6),
  recordingsDir: settings.outputDir,
  downloadsDir: downloadsDir()
}));
ipcMain.handle('library:open', (_e, p) => shell.openPath(p));
ipcMain.handle('library:reveal', (_e, p) => shell.showItemInFolder(p));
ipcMain.handle('library:delete', (_e, p) => { try { fs.rmSync(p); return true; } catch { return false; } });
ipcMain.handle('library:openFolder', (_e, which) =>
  shell.openPath(which === 'downloads' ? downloadsDir() : settings.outputDir));
ipcMain.handle('shell:openExternal', (_e, url) => {
  if (/^https?:\/\//i.test(url)) shell.openExternal(url);
});
ipcMain.handle('ffmpeg:check', () => hasFfmpeg());

/* ================= lifecycle ================= */
app.whenReady().then(() => {
  // The app's pages are loaded from file://, and Chromium refuses camera and
  // microphone to that origin unless the app answers for it -- without these
  // two handlers getUserMedia fails with "Permission denied".
  //
  // A file:// iframe (the module frame) reports its origin as the opaque
  // string "null", so an origin check alone would refuse the recorder the
  // very devices it exists to use. Fall back to the URL of the WebContents,
  // which is always one of our own pages. Only local content is trusted:
  // the only remote thing this app loads is a service on 127.0.0.1.
  const MEDIA = new Set(['media', 'audioCapture', 'videoCapture', 'display-capture']);
  // The recap tool tells you when a step that takes minutes has finished. The
  // handler below answers for every permission the app asks for, so leaving
  // notifications out of this set is the same as denying them.
  const ALLOWED = new Set([...MEDIA, 'notifications']);
  const isLocal = u =>
    typeof u === 'string' &&
    (u.startsWith('file://') || u.startsWith('http://127.0.0.1') || u.startsWith('http://localhost'));

  const allowMedia = (perm, wc, ...urls) => {
    if (!ALLOWED.has(perm)) return false;
    // an opaque ("null") or empty origin says nothing -- judge by the page
    if (urls.some(isLocal)) return true;
    return isLocal(wc ? wc.getURL() : '');
  };

  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((wc, perm, cb, details) =>
    cb(allowMedia(perm, wc, details && details.requestingUrl, details && details.securityOrigin)));
  ses.setPermissionCheckHandler((wc, perm, origin, details) =>
    allowMedia(perm, wc, origin, details && details.requestingUrl, details && details.securityOrigin));

  loadSettings();
  loadManifest();
  createMainWindow();
  registerHotkeys();

  try {
    const iconPath = path.join(__dirname, 'renderer', 'icon.png');
    const img = fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
    tray = new Tray(img);
    tray.setToolTip('Toolbox');
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: 'Open Toolbox', click: () => { if (!mainWindow) createMainWindow(); else { mainWindow.show(); mainWindow.focus(); } } },
      { label: 'Start / Stop recording', click: () => send('hotkey', 'startStop') },
      { type: 'separator' },
      { label: 'Quit', click: () => app.quit() }
    ]));
    tray.on('double-click', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } });
  } catch {}

  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createMainWindow(); });
});

app.on('will-quit', () => { globalShortcut.unregisterAll(); stopAllServices(); });
app.on('before-quit', stopAllServices);
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
