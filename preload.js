const { contextBridge, ipcRenderer } = require('electron');

/* ------------------------------------------------------------------
   The shell and every module frame share this bridge. Broadcast IPC
   (hotkeys, panel buttons, encode progress) arrives in the top frame
   only, so it is relayed down to child frames by postMessage and the
   subscribe helpers below listen to both paths.
------------------------------------------------------------------ */
const BROADCAST = ['hotkey', 'panel:action', 'convert:progress', 'service:state', 'cam:state'];
const subs = {};

function dispatch(channel, payload) {
  (subs[channel] || []).forEach(fn => { try { fn(payload); } catch (e) { console.error(e); } });
  // relay to child frames (the module iframe)
  for (let i = 0; i < window.frames.length; i++) {
    try { window.frames[i].postMessage({ __toolbox: true, channel, payload }, '*'); } catch {}
  }
}

BROADCAST.forEach(ch => ipcRenderer.on(ch, (_e, payload) => dispatch(ch, payload)));

window.addEventListener('message', e => {
  const d = e.data;
  if (d && d.__toolbox && BROADCAST.includes(d.channel)) dispatch(d.channel, d.payload);
});

const on = (channel, cb) => { (subs[channel] = subs[channel] || []).push(cb); };

contextBridge.exposeInMainWorld('api', {
  /* shell */
  getModules: () => ipcRenderer.invoke('app:modules'),
  getVersions: () => ipcRenderer.invoke('app:version'),
  openExternal: url => ipcRenderer.invoke('shell:openExternal', url),

  /* background services */
  startService: id => ipcRenderer.invoke('service:start', id),
  stopService: id => ipcRenderer.invoke('service:stop', id),
  serviceStatus: id => ipcRenderer.invoke('service:status', id),
  onServiceState: cb => on('service:state', cb),

  /* settings */
  getSettings: () => ipcRenderer.invoke('settings:get'),
  setSettings: s => ipcRenderer.invoke('settings:set', s),
  pickDir: () => ipcRenderer.invoke('settings:pickDir'),

  /* capture */
  listSources: () => ipcRenderer.invoke('sources:list'),
  getDisplays: () => ipcRenderer.invoke('displays:get'),
  selectRegion: mode => ipcRenderer.invoke('region:select', mode),
  sendRegion: rect => ipcRenderer.send('region:result', rect),

  /* window + floating panel */
  hideWindow: () => ipcRenderer.send('window:hide'),
  showWindow: () => ipcRenderer.send('window:show'),
  openPanel: state => ipcRenderer.send('panel:open', state),
  updatePanel: state => ipcRenderer.send('panel:update', state),
  closePanel: () => ipcRenderer.send('panel:close'),
  panelAction: action => ipcRenderer.send('panel:action', action),
  onPanelState: cb => ipcRenderer.on('panel:state', (_e, s) => cb(s)),
  onPanelAction: cb => on('panel:action', cb),

  /* floating camera bubble */
  openCam: opts => ipcRenderer.invoke('cam:open', opts || {}),
  closeCam: () => ipcRenderer.invoke('cam:close'),
  setCamVisible: v => ipcRenderer.invoke('cam:setVisible', v),
  getCamState: () => ipcRenderer.invoke('cam:state'),
  onCamState: cb => on('cam:state', cb),
  onCamDevice: cb => ipcRenderer.on('cam:device', (_e, id) => cb(id)),

  /* output */
  saveRecording: payload => ipcRenderer.invoke('recording:save', payload),
  saveScreenshot: payload => ipcRenderer.invoke('screenshot:save', payload),
  onConvertProgress: cb => on('convert:progress', cb),

  /* files */
  listLibrary: () => ipcRenderer.invoke('library:list'),
  recentFiles: limit => ipcRenderer.invoke('library:recent', limit),
  openFile: p => ipcRenderer.invoke('library:open', p),
  revealFile: p => ipcRenderer.invoke('library:reveal', p),
  deleteFile: p => ipcRenderer.invoke('library:delete', p),
  openFolder: which => ipcRenderer.invoke('library:openFolder', which),
  checkFfmpeg: () => ipcRenderer.invoke('ffmpeg:check'),

  /* hotkeys */
  onHotkey: cb => on('hotkey', cb),

  /* module frame -> shell navigation */
  navigate: moduleId => window.parent.postMessage({ __toolboxNav: moduleId }, '*')
});
