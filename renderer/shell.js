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

/* ---------- SVG Icons (UI-UX Pro Max Vector Standards) ---------- */
function getModuleSvg(id, size = 18) {
  if (id === 'recap-studio' || id === 'recap') {
    return `<svg class="svg-ic ic-${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m15 4 4 4"/><path d="m20 9-8.5 8.5a2.12 2.12 0 1 1-3-3L17 6"/><path d="m3 21 3-3"/><path d="M19 15v4"/><path d="M17 17h4"/><path d="M9 3v4"/><path d="M7 5h4"/></svg>`;
  }
  if (id === 'screen-recorder' || id === 'recorder') {
    return `<svg class="svg-ic ic-${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m22 8-6 4 6 4V8Z"/><rect width="14" height="12" x="2" y="6" rx="2"/><circle cx="9" cy="12" r="2" fill="currentColor"/></svg>`;
  }
  if (id === 'youtube-downloader' || id === 'downloader') {
    return `<svg class="svg-ic ic-${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="3" y2="15"/></svg>`;
  }
  return `<svg class="svg-ic ic-${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>`;
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
      '<span class="ic" aria-hidden="true">' + getModuleSvg(m.id, 18) + '</span> ' +
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
                     '<div class="tool-ic" aria-hidden="true">' + getModuleSvg(m.id, 24) + '</div>';
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
      '<div class="tool-go">Open <svg class="svg-ic ic-14" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg></div>';

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
      '<div class="tool-ic" aria-hidden="true"><svg class="svg-ic ic-20" viewBox="0 0 24 24" fill="none" stroke="currentColor"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></div>' +
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

function renderRecent(hostId, items, emptyTitle, emptyDesc, iconType) {
  const host = $(hostId);
  host.innerHTML = '';
  if (!items.length) {
    const emptySvg = iconType === 'recordings'
      ? '<svg class="svg-ic ic-24" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="m22 8-6 4 6 4V8Z"/><rect width="14" height="12" x="2" y="6" rx="2"/></svg>'
      : '<svg class="svg-ic ic-24" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="3" y2="15"/></svg>';
    host.innerHTML =
      '<div class="empty-state" style="padding:28px 16px;border:none;background:transparent">' +
        '<div class="empty-state-ic" style="width:44px;height:44px;margin-bottom:10px;">' + emptySvg + '</div>' +
        '<div class="empty-state-title" style="font-size:13.5px;margin-bottom:4px;">' + emptyTitle + '</div>' +
        '<div class="empty-state-desc" style="font-size:12px;margin-bottom:0;">' + emptyDesc + '</div>' +
      '</div>';
    return;
  }
  items.forEach(it => {
    const row = document.createElement('div');
    row.className = 'rec-row';
    row.setAttribute('tabindex', '0');
    row.setAttribute('role', 'article');
    row.setAttribute('aria-label', it.name + ', size ' + fmtSize(it.size));

    const icon = ['.png', '.jpg'].includes(it.ext)
      ? '<svg class="svg-ic ic-16" viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>'
      : ['.m4a', '.mp3'].includes(it.ext)
      ? '<svg class="svg-ic ic-16" viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>'
      : '<svg class="svg-ic ic-16" viewBox="0 0 24 24" fill="none" stroke="currentColor"><polygon points="6 3 20 12 6 21 6 3"/></svg>';

    row.innerHTML =
      '<div class="rec-ic" aria-hidden="true">' + icon + '</div>' +
      '<div class="rec-txt"><div class="rec-name"></div><div class="rec-sub tabular-nums"></div></div>' +
      '<button class="rec-btn" aria-label="Reveal in folder" title="Show in folder">' +
        '<svg class="svg-ic ic-14" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 8 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></svg>' +
        '<span>Folder</span>' +
      '</button>';
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
    renderRecent('recentRecordings', r.recordings || [], 'No recordings yet', 'Capture your screen, webcam or audio to start.', 'recordings');
    renderRecent('recentDownloads', r.downloads || [], 'No downloads yet', 'Paste video URLs to save high quality media.', 'downloads');
  } catch (err) {
    renderRecent('recentRecordings', [], 'Could not load recordings', 'Check folder permissions or settings.', 'recordings');
    renderRecent('recentDownloads', [], 'Could not load downloads', 'Check folder permissions or settings.', 'downloads');
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
