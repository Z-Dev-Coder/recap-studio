/* Toolbox shell: dashboard, sidebar and the module host frame. */
const $ = id => document.getElementById(id);
const qsa = s => Array.from(document.querySelectorAll(s));

let modules = [];
let current = null;          // active module object
const svcState = {};         // serviceId -> status

/* ---------- helpers ---------- */
function fmtSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(0) + ' KB';
  if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB';
  return (b / 1073741824).toFixed(2) + ' GB';
}
function ago(ms) {
  const s = (Date.now() - ms) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + ' min ago';
  if (s < 86400) return Math.floor(s / 3600) + ' h ago';
  if (s < 604800) return Math.floor(s / 86400) + ' d ago';
  return new Date(ms).toLocaleDateString();
}
function greeting() {
  const h = new Date().getHours();
  return h < 5 ? 'Still up?' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}

/* ---------- sidebar + cards ---------- */
function renderNav() {
  const host = $('navModules');
  host.innerHTML = '';
  modules.forEach(m => {
    const b = document.createElement('button');
    b.className = 'nav-item';
    b.dataset.view = m.id;
    b.innerHTML = '<span class="ic" style="color:' + m.accent + '">' + m.icon + '</span> ' +
                  '<span class="lbl"></span><span class="dotc" data-svc="' + (m.service || '') + '"></span>';
    b.querySelector('.lbl').textContent = m.name;
    b.onclick = () => openModule(m.id);
    host.appendChild(b);
  });
  $('nav').querySelector('[data-view="dashboard"]').onclick = showDashboard;
}

function renderCards() {
  const grid = $('toolGrid');
  grid.innerHTML = '';
  modules.forEach(m => {
    const c = document.createElement('button');
    c.className = 'tool-card';
    c.style.setProperty('--tint', m.accent || '#4c8dff');
    c.innerHTML =
      '<div class="tool-ic">' + m.icon + '</div>' +
      '<div class="tool-name"></div>' +
      '<div class="tool-tag"></div>' +
      '<div class="tool-go">Open &#8594;</div>';
    c.querySelector('.tool-name').textContent = m.name;
    c.querySelector('.tool-tag').textContent = m.tagline || '';
    c.onclick = () => openModule(m.id);
    grid.appendChild(c);
  });

  const soon = document.createElement('div');
  soon.className = 'tool-card soon';
  soon.style.setProperty('--tint', '#5f6b80');
  soon.innerHTML =
    '<div class="tool-ic">+</div>' +
    '<div class="tool-name">Add a tool</div>' +
    '<div class="tool-tag">Drop a folder in <b>modules\\</b> and add it to modules\\manifest.json.</div>';
  grid.appendChild(soon);
}

function markActive(view) {
  qsa('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === view));
}

/* ---------- views ---------- */
function showDashboard() {
  $('view-dashboard').classList.add('active');
  $('view-module').classList.remove('active');
  markActive('dashboard');
  loadRecent();
}

async function openModule(id) {
  const m = modules.find(x => x.id === id);
  if (!m) return;
  current = m;

  $('view-dashboard').classList.remove('active');
  $('view-module').classList.add('active');
  markActive(id);
  $('moduleTitle').textContent = m.name;
  $('moduleState').textContent = '';
  $('moduleError').classList.add('hidden');

  const frame = $('moduleFrame');

  if (m.type === 'service-web') {
    // reuse a frame that is already showing this service
    const known = await window.api.serviceStatus(m.service);
    if (known.status === 'ready' && frame.dataset.module === m.id) return;

    showLoader('Starting ' + m.name + '...');
    frame.dataset.module = '';
    frame.src = 'about:blank';
    const res = await window.api.startService(m.service);
    if (!res.ok) {
      hideLoader();
      showError(m, res.error);
      return;
    }
    frame.dataset.module = m.id;
    frame.src = res.url;
    frame.onload = () => { hideLoader(); $('moduleState').textContent = res.url; };
    return;
  }

  if (frame.dataset.module === m.id) return;   // keep recorder state alive
  showLoader('Loading ' + m.name + '...');
  frame.dataset.module = m.id;
  frame.src = '../' + m.entry;
  frame.onload = () => hideLoader();
}

function showLoader(text) {
  $('loaderText').textContent = text;
  $('moduleLoader').classList.remove('hidden');
}
function hideLoader() { $('moduleLoader').classList.add('hidden'); }

function showError(m, message) {
  const box = $('moduleError');
  box.classList.remove('hidden');
  box.innerHTML =
    '<div class="err-box">' +
      '<h3>' + m.name + ' could not start</h3>' +
      '<pre></pre>' +
      '<button class="ghost" id="retryModule">Try again</button> ' +
      '<button class="ghost" id="errHome">Back to dashboard</button>' +
    '</div>';
  box.querySelector('pre').textContent = message;
  $('retryModule').onclick = () => { box.classList.add('hidden'); openModule(m.id); };
  $('errHome').onclick = () => { box.classList.add('hidden'); showDashboard(); };
}

$('backHome').onclick = showDashboard;
$('moduleReload').onclick = () => {
  const f = $('moduleFrame');
  const src = f.src;
  f.src = 'about:blank';
  setTimeout(() => { f.src = src; }, 60);
};

/* ---------- recent files ---------- */
function renderRecent(hostId, items, emptyText) {
  const host = $(hostId);
  host.innerHTML = '';
  if (!items.length) {
    host.innerHTML = '<div class="empty">' + emptyText + '</div>';
    return;
  }
  items.forEach(it => {
    const row = document.createElement('div');
    row.className = 'rec-row';
    const icon = ['.png', '.jpg'].includes(it.ext) ? '&#9635;'
               : ['.m4a', '.mp3'].includes(it.ext) ? '&#9834;' : '&#9654;';
    row.innerHTML =
      '<div class="rec-ic">' + icon + '</div>' +
      '<div class="rec-txt"><div class="rec-name"></div><div class="rec-sub"></div></div>' +
      '<button class="rec-btn">Folder</button>';
    row.querySelector('.rec-name').textContent = it.name;
    row.querySelector('.rec-sub').textContent = fmtSize(it.size) + '  ·  ' + ago(it.mtime);
    row.onclick = () => window.api.openFile(it.path);
    row.querySelector('.rec-btn').onclick = e => { e.stopPropagation(); window.api.revealFile(it.path); };
    host.appendChild(row);
  });
}

async function loadRecent() {
  const r = await window.api.recentFiles(7);
  renderRecent('recentRecordings', r.recordings, 'Nothing recorded yet.');
  renderRecent('recentDownloads', r.downloads, 'Nothing downloaded yet.');
}

qsa('[data-folder]').forEach(b => { b.onclick = () => window.api.openFolder(b.dataset.folder); });

/* ---------- service indicators ---------- */
function renderServices() {
  const host = $('svcDots');
  host.innerHTML = '';
  modules.filter(m => m.service).forEach(m => {
    const st = svcState[m.service] || 'stopped';
    const d = document.createElement('div');
    d.className = 'svc';
    d.innerHTML = '<i class="' + st + '"></i><span></span>';
    d.querySelector('span').textContent = m.name + ' · ' + st;
    host.appendChild(d);
    const dot = document.querySelector('.dotc[data-svc="' + m.service + '"]');
    if (dot) dot.className = 'dotc ' + st;
  });
}

window.api.onServiceState(s => {
  if (!s) return;
  svcState[s.id] = s.status;
  renderServices();
  if (s.status === 'stopped' && current && current.service === s.id) {
    $('moduleState').textContent = 'service stopped';
  }
});

/* module frames can ask the shell to navigate */
window.addEventListener('message', e => {
  if (e.data && e.data.__toolboxNav) {
    e.data.__toolboxNav === 'dashboard' ? showDashboard() : openModule(e.data.__toolboxNav);
  }
});

/* a recording started by hotkey while the recorder frame was never opened
   still needs the module loaded, so make sure it is mounted on first hotkey */
window.api.onHotkey(() => {
  const rec = modules.find(m => m.id === 'screen-recorder');
  if (rec && $('moduleFrame').dataset.module !== rec.id) openModule(rec.id);
});

/* ---------- boot ---------- */
(async function init() {
  $('greeting').textContent = greeting();
  modules = await window.api.getModules();
  renderNav();
  renderCards();
  renderServices();
  await loadRecent();

  const v = await window.api.getVersions();
  $('ver').textContent = 'v' + v.app + ' · Electron ' + v.electron;

  const s = await window.api.getSettings();
  if (s.lastModule && s.lastModule !== 'dashboard' && modules.some(m => m.id === s.lastModule)) {
    // only pre-mount the recorder; services start on demand
    if (s.lastModule === 'screen-recorder') openModule(s.lastModule);
  }
})();

window.addEventListener('beforeunload', () => {
  if (current) window.api.setSettings({ lastModule: current.id });
});
