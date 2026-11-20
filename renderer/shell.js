/* Toolbox shell: modern Bento Grid dashboard, glassmorphism sidebar, and module host frame. */
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
    b.setAttribute('aria-label', m.name);
    b.innerHTML =
      '<span class="ic" aria-hidden="true">' + m.icon + '</span> ' +
      '<span class="lbl"></span>' +
      '<span class="dotc" data-svc="' + (m.service || '') + '" aria-hidden="true"></span>';
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
    c.style.setProperty('--tint', m.accent || 'var(--accent-start)');
    c.setAttribute('aria-label', 'Open ' + m.name + ' — ' + (m.tagline || ''));

    // Apply asymmetric Bento grid span classes
    const isFeatured = m.id === 'recap-studio' || m.id === 'recap';
    const isWide = m.id === 'screen-recorder' || m.id === 'youtube-downloader' ||
                   m.id === 'recorder' || m.id === 'downloader';

    if (isFeatured) {
      c.classList.add('featured');
    } else if (isWide) {
      c.classList.add('wide');
    }

    let headHtml = '<div class="tool-card-head">' +
                     '<div class="tool-ic" aria-hidden="true">' + m.icon + '</div>';
    if (isFeatured) {
      headHtml += '<span class="bento-badge">AI Studio</span>';
    }
    headHtml += '</div>';

    let previewHtml = '';
    if (isFeatured) {
      previewHtml =
        '<div class="bento-preview" aria-hidden="true">' +
          '<div class="bento-bar bento-audio" title="Audio Layer"></div>' +
          '<div class="bento-bar bento-text" title="AI Transcript & Script"></div>' +
          '<div class="bento-bar bento-video" title="Recap Video Output"></div>' +
        '</div>';
    }

    c.innerHTML =
      headHtml +
      '<div class="tool-name"></div>' +
      '<div class="tool-tag"></div>' +
      previewHtml +
      '<div class="tool-go">Open <span aria-hidden="true">&#8594;</span></div>';

    c.querySelector('.tool-name').textContent = m.name;
    c.querySelector('.tool-tag').textContent = m.tagline || '';

    // Click & Keyboard support
    c.onclick = () => openModule(m.id);
    c.onkeydown = e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openModule(m.id);
      }
    };

    grid.appendChild(c);
  });

  // "Add a tool" placeholder card
  const soon = document.createElement('div');
  soon.className = 'tool-card soon';
  soon.style.setProperty('--tint', 'var(--text-tertiary)');
  soon.setAttribute('aria-label', 'Add a tool placeholder');
  soon.innerHTML =
    '<div class="tool-card-head">' +
      '<div class="tool-ic" aria-hidden="true">+</div>' +
    '</div>' +
    '<div class="tool-name">Add a tool</div>' +
    '<div class="tool-tag">Drop a folder in <b>modules\\</b> and register it in <code>modules\\manifest.json</code>.</div>';
  grid.appendChild(soon);
}

function markActive(view) {
  qsa('.nav-item').forEach(b => {
    const isActive = b.dataset.view === view;
    b.classList.toggle('active', isActive);
    b.setAttribute('aria-current', isActive ? 'page' : 'false');
  });
}

/* ---------- views ---------- */
function showDashboard() {
  current = null;
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

  const titleEl = $('moduleTitle');
  titleEl.textContent = m.name;
  titleEl.focus(); // Focus management for accessibility

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
    const targetUrl = res.url + (m.path || '');
    frame.src = targetUrl + (targetUrl.includes('?') ? '&' : '?') + 'v=' + Date.now();
    frame.onload = () => { hideLoader(); $('moduleState').textContent = targetUrl; };
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

function hideLoader() {
  $('moduleLoader').classList.add('hidden');
}

function showError(m, message) {
  const box = $('moduleError');
  box.classList.remove('hidden');
  box.innerHTML =
    '<div class="err-box" role="alertdialog" aria-labelledby="errTitle">' +
      '<h3 id="errTitle">' + m.name + ' could not start</h3>' +
      '<pre></pre>' +
      '<div class="row" style="margin-top:14px;gap:10px">' +
        '<button class="primary-btn" id="retryModule">Try again</button> ' +
        '<button class="ghost" id="errHome">Back to dashboard</button>' +
      '</div>' +
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

// Global escape key handler to return to dashboard
window.addEventListener('keydown', e => {
  if (e.key === 'Escape' && current) {
    showDashboard();
  }
});

/* ---------- recent files ---------- */
function renderRecentSkeletons(hostId) {
  const host = $(hostId);
  host.innerHTML = '';
  for (let i = 0; i < 3; i++) {
    const row = document.createElement('div');
    row.className = 'rec-skeleton';
    row.innerHTML =
      '<div class="sk-box" style="width:32px;height:32px;flex-shrink:0"></div>' +
      '<div style="flex:1;display:flex;flex-direction:column;gap:6px">' +
        '<div class="sk-box" style="width:60%;height:12px"></div>' +
        '<div class="sk-box" style="width:35%;height:10px"></div>' +
      '</div>';
    host.appendChild(row);
  }
}

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
    row.setAttribute('tabindex', '0');
    row.setAttribute('role', 'article');
    row.setAttribute('aria-label', it.name + ', size ' + fmtSize(it.size));

    const icon = ['.png', '.jpg'].includes(it.ext) ? '&#9635;'
               : ['.m4a', '.mp3'].includes(it.ext) ? '&#9834;' : '&#9654;';
    row.innerHTML =
      '<div class="rec-ic" aria-hidden="true">' + icon + '</div>' +
      '<div class="rec-txt"><div class="rec-name"></div><div class="rec-sub"></div></div>' +
      '<button class="rec-btn" aria-label="Reveal in folder">Folder</button>';
    row.querySelector('.rec-name').textContent = it.name;
    row.querySelector('.rec-sub').textContent = fmtSize(it.size) + '  ·  ' + ago(it.mtime);

    row.onclick = () => window.api.openFile(it.path);
    row.onkeydown = e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        window.api.openFile(it.path);
      }
    };

    const btn = row.querySelector('.rec-btn');
    btn.onclick = e => { e.stopPropagation(); window.api.revealFile(it.path); };
    btn.onkeydown = e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.stopPropagation();
        e.preventDefault();
        window.api.revealFile(it.path);
      }
    };

    host.appendChild(row);
  });
}

async function loadRecent() {
  renderRecentSkeletons('recentRecordings');
  renderRecentSkeletons('recentDownloads');
  try {
    const r = await window.api.recentFiles(7);
    renderRecent('recentRecordings', r.recordings || [], 'Nothing recorded yet.');
    renderRecent('recentDownloads', r.downloads || [], 'Nothing downloaded yet.');
  } catch (err) {
    renderRecent('recentRecordings', [], 'Could not load recordings.');
    renderRecent('recentDownloads', [], 'Could not load downloads.');
  }
}

qsa('[data-folder]').forEach(b => {
  b.onclick = () => window.api.openFolder(b.dataset.folder);
});

/* ---------- service indicators ---------- */
function renderServices() {
  const host = $('svcDots');
  host.innerHTML = '';
  modules.filter(m => m.service).forEach(m => {
    const st = svcState[m.service] || 'stopped';
    const d = document.createElement('div');
    d.className = 'svc';
    d.setAttribute('role', 'status');
    d.innerHTML = '<i class="' + st + '" aria-hidden="true"></i><span></span>';
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
