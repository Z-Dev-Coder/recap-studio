/* ------------------------------------------------------------------
   Screen Recorder - renderer logic
------------------------------------------------------------------ */
const $ = id => document.getElementById(id);
const qsa = s => Array.from(document.querySelectorAll(s));

const state = {
  settings: null,
  mode: 'fullscreen',
  sources: [],
  selectedSourceId: null,
  region: null,
  displays: null,
  recording: false,
  paused: false,
  startedAt: 0,
  elapsed: 0,
  timerId: null,
  autoStopId: null,
  busy: false
};

const rt = {           // live recording resources
  recorder: null, chunks: [], streams: [], audioCtx: null,
  canvas: null, raf: null, camVideo: null, screenVideo: null, drawTimer: null
};

const preview = { camStream: null, micStream: null, micRaf: null, micCtx: null };

/* live geometry of the floating camera bubble, so the composite can put the
   face exactly where the user dragged it */
state.camBubble = { open: false, visible: false, bounds: null };
window.api.onCamState(st => { state.camBubble = st || { open: false, visible: false, bounds: null }; });

/* Map the bubble's screen rectangle into canvas pixels.

   The canvas is either a whole display (full screen) or the selected region,
   both known in screen coordinates, so the bubble can be placed by simple
   scaling. A captured single window has no such mapping -- there the caller
   falls back to the fixed corner. */
function bubbleOnCanvas(canvas, sourceId) {
  const st = state.camBubble;
  if (!st || !st.open || !st.visible || !st.bounds) return null;
  if (state.mode === 'window') return null;

  const displays = (state.displays && state.displays.displays) || [];
  let base = state.region;
  if (!base) {
    const src = (state.sources || []).find(x => x.id === sourceId);
    const d = (src && displays.find(dd => String(dd.id) === String(src.display_id))) ||
              displays.find(dd => dd.primary) || displays[0];
    if (!d) return null;
    base = { x: d.bounds.x, y: d.bounds.y, width: d.bounds.width, height: d.bounds.height };
  }
  if (!base || !base.width || !base.height) return null;

  const sx = canvas.width / base.width;
  const sy = canvas.height / base.height;
  const b = st.bounds;
  return {
    x: (b.x - base.x) * sx,
    y: (b.y - base.y) * sy,
    w: b.width * sx,
    h: b.height * sy
  };
}

/* ---------------- helpers ---------------- */
function fmtTime(sec) {
  const h = String(Math.floor(sec / 3600)).padStart(2, '0');
  const m = String(Math.floor(sec / 60) % 60).padStart(2, '0');
  const s = String(Math.floor(sec) % 60).padStart(2, '0');
  return h + ':' + m + ':' + s;
}
function fmtSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(0) + ' KB';
  if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB';
  return (b / 1073741824).toFixed(2) + ' GB';
}
/* Windows can block mic/camera for every desktop app at the OS level. Chromium
   reports that as a plain NotAllowedError, which reads like an app bug -- say
   what actually has to be changed instead. */
function deviceError(what, e) {
  const os = e && e.name === 'NotAllowedError' && /system/i.test(e.message || '');
  if (os) {
    return what + ' blocked by Windows. Open Settings > Privacy & security > ' +
           what + ', turn on "' + what + ' access" and "Let desktop apps access your ' +
           what.toLowerCase() + '", then click Reload.';
  }
  if (e && e.name === 'NotFoundError') return 'No ' + what.toLowerCase() + ' found.';
  return what + ' unavailable: ' + (e && e.message ? e.message : e);
}

function setStatus(msg, kind) {
  const el = $('status');
  el.textContent = msg || '';
  el.className = 'status' + (kind ? ' ' + kind : '');
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ---------------- tabs ---------------- */
qsa('.nav-item').forEach(btn => {
  btn.onclick = () => {
    qsa('.nav-item').forEach(b => b.classList.remove('active'));
    qsa('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    $('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'library') loadLibrary();
  };
});

/* ---------------- modes ---------------- */
qsa('.mode-card').forEach(card => {
  card.onclick = () => {
    if (state.recording) return;
    qsa('.mode-card').forEach(c => c.classList.remove('active'));
    card.classList.add('active');
    state.mode = card.dataset.mode;
    applyMode();
  };
});

function applyMode() {
  const m = state.mode;
  const needsPicker = m === 'fullscreen' || m === 'window' || m === 'region';
  const listCard = $('sourceList');
  const regionBox = $('regionInfo');

  listCard.classList.toggle('hidden', !needsPicker || m === 'region');
  regionBox.classList.toggle('hidden', m !== 'region');

  $('pickerTitle').textContent =
    m === 'window' ? 'Choose a window' :
    m === 'region' ? 'Capture area' :
    m === 'webcam' ? 'Webcam' :
    m === 'audio' ? 'Audio sources' :
    m === 'screenshot' ? 'Screenshot' : 'Choose a display';

  $('recordLabel').textContent =
    m === 'screenshot' ? 'Take screenshot' :
    m === 'audio' ? 'Start audio recording' : 'Start recording';

  // webcam mode implies the camera is the capture source
  if (m === 'webcam') { $('tCam').checked = true; onCamToggle(); }

  if (needsPicker && m !== 'region') renderSources();
}

/* ---------------- sources ---------------- */
async function loadSources() {
  state.sources = await window.api.listSources();
  state.displays = await window.api.getDisplays();
  renderSources();
}

function renderSources() {
  const wanted = state.mode === 'window' ? 'window' : 'screen';
  const list = state.sources.filter(s => s.type === wanted);
  const el = $('sourceList');
  el.innerHTML = '';
  if (!list.length) {
    el.innerHTML = '<div class="empty">Nothing to show. Click Refresh.</div>';
    return;
  }
  if (!list.some(s => s.id === state.selectedSourceId)) state.selectedSourceId = list[0].id;

  list.forEach(s => {
    const b = document.createElement('button');
    b.className = 'src' + (s.id === state.selectedSourceId ? ' active' : '');
    b.innerHTML =
      (s.thumbnail ? '<img src="' + s.thumbnail + '" alt="" />' : '<div style="height:88px"></div>') +
      '<div class="nm"></div>';
    b.querySelector('.nm').textContent = s.name;
    b.onclick = () => { state.selectedSourceId = s.id; renderSources(); };
    el.appendChild(b);
  });
}
$('refreshSources').onclick = loadSources;

/* ---------------- region ---------------- */
async function pickRegion() {
  const r = await window.api.selectRegion(state.mode);
  if (r && r.fullscreen) {
    state.region = null;
    $('regionText').textContent = 'Full screen (no crop)';
    return true;
  }
  if (!r) { setStatus('Selection cancelled.'); return false; }
  state.region = r;
  $('regionText').textContent =
    'Area ' + r.width + ' x ' + r.height + '  at  ' + r.x + ', ' + r.y;
  return true;
}
$('pickRegion').onclick = pickRegion;

/* ---------------- devices ---------------- */
async function loadDevices() {
  let devices = [];
  try { devices = await navigator.mediaDevices.enumerateDevices(); } catch {}
  const mics = devices.filter(d => d.kind === 'audioinput');
  const cams = devices.filter(d => d.kind === 'videoinput');

  const fill = (sel, items, label) => {
    sel.innerHTML = '';
    if (!items.length) {
      sel.innerHTML = '<option value="">No ' + label + ' found</option>';
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    items.forEach((d, i) => {
      const o = document.createElement('option');
      o.value = d.deviceId;
      o.textContent = d.label || (label + ' ' + (i + 1));
      sel.appendChild(o);
    });
  };
  fill($('micDevice'), mics, 'microphone');
  fill($('camDevice'), cams, 'camera');
}

/* mic level meter */
async function startMicMeter() {
  stopMicMeter();
  if (!$('tMic').checked) return;
  try {
    const id = $('micDevice').value;
    preview.micStream = await navigator.mediaDevices.getUserMedia({
      audio: id ? { deviceId: { exact: id } } : true
    });
    await loadDevices();
    preview.micCtx = new AudioContext();
    const src = preview.micCtx.createMediaStreamSource(preview.micStream);
    const an = preview.micCtx.createAnalyser();
    an.fftSize = 512;
    src.connect(an);
    const data = new Uint8Array(an.frequencyBinCount);
    const tick = () => {
      an.getByteTimeDomainData(data);
      let peak = 0;
      for (let i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128));
      $('micMeter').style.width = Math.min(100, (peak / 90) * 100) + '%';
      preview.micRaf = requestAnimationFrame(tick);
    };
    tick();
  } catch (e) { setStatus(deviceError('Microphone', e), 'err'); }
}
function stopMicMeter() {
  if (preview.micRaf) cancelAnimationFrame(preview.micRaf);
  preview.micRaf = null;
  if (preview.micCtx) { preview.micCtx.close().catch(() => {}); preview.micCtx = null; }
  if (preview.micStream) { preview.micStream.getTracks().forEach(t => t.stop()); preview.micStream = null; }
  $('micMeter').style.width = '0%';
}

async function onCamToggle() {
  const on = $('tCam').checked;
  $('camDevice').disabled = !on;
  if (preview.camStream) { preview.camStream.getTracks().forEach(t => t.stop()); preview.camStream = null; }
  $('camPreview').srcObject = null;
  if (!on) {
    window.api.closeCam();
    return;
  }
  try {
    const id = $('camDevice').value;
    preview.camStream = await navigator.mediaDevices.getUserMedia({
      video: id ? { deviceId: { exact: id }, width: 1280, height: 720 } : { width: 1280, height: 720 }
    });
    $('camPreview').srcObject = preview.camStream;
    await loadDevices();
    // Bring the bubble up now rather than at record time: placing your face is
    // something you want to do before the countdown, not during the take.
    if (state.mode !== 'webcam') window.api.openCam({ deviceId: $('camDevice').value });
  } catch (e) { setStatus(deviceError('Camera', e), 'err'); $('tCam').checked = false; }
}

/* ---------------- capture streams ---------------- */
async function getDesktopStream(sourceId, withSystemAudio, fps) {
  const video = {
    mandatory: {
      chromeMediaSource: 'desktop',
      chromeMediaSourceId: sourceId,
      maxFrameRate: fps,
      maxWidth: 3840,
      maxHeight: 2160
    }
  };
  // system audio and video must be requested together for WGC loopback
  if (withSystemAudio) {
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: { mandatory: { chromeMediaSource: 'desktop' } },
        video
      });
    } catch (e) {
      setStatus('System sound not available on this source, continuing without it.');
    }
  }
  return navigator.mediaDevices.getUserMedia({ audio: false, video });
}

async function getMicStream() {
  const id = $('micDevice').value;
  return navigator.mediaDevices.getUserMedia({
    audio: id
      ? { deviceId: { exact: id }, echoCancellation: false, noiseSuppression: true, autoGainControl: true }
      : { echoCancellation: false, noiseSuppression: true }
  });
}

async function getCamStream() {
  const id = $('camDevice').value;
  return navigator.mediaDevices.getUserMedia({
    video: id ? { deviceId: { exact: id }, width: 1280, height: 720 } : { width: 1280, height: 720 }
  });
}

function mixAudio(streams) {
  const live = streams.filter(s => s && s.getAudioTracks().length);
  if (!live.length) return { track: null, ctx: null };
  const ctx = new AudioContext();
  const dest = ctx.createMediaStreamDestination();
  live.forEach(s => {
    const src = ctx.createMediaStreamSource(new MediaStream(s.getAudioTracks()));
    const gain = ctx.createGain();
    gain.gain.value = 1;
    src.connect(gain).connect(dest);
  });
  return { track: dest.stream.getAudioTracks()[0], ctx };
}

function pickMime() {
  const candidates = [
    'video/webm;codecs=vp9,opus',
    'video/webm;codecs=vp8,opus',
    'video/webm;codecs=h264,opus',
    'video/webm'
  ];
  return candidates.find(c => MediaRecorder.isTypeSupported(c)) || 'video/webm';
}

/* map a virtual-screen region onto the captured video pixels */
function cropForRegion(region, videoW, videoH) {
  if (!region) return null;
  const displays = (state.displays && state.displays.displays) || [];
  const d = displays.find(dd =>
    region.x >= dd.bounds.x && region.y >= dd.bounds.y &&
    region.x < dd.bounds.x + dd.bounds.width && region.y < dd.bounds.y + dd.bounds.height
  ) || displays[0];
  if (!d) return null;
  const sx = videoW / d.bounds.width;
  const sy = videoH / d.bounds.height;
  return {
    x: Math.max(0, Math.round((region.x - d.bounds.x) * sx)),
    y: Math.max(0, Math.round((region.y - d.bounds.y) * sy)),
    w: Math.min(videoW, Math.round(region.width * sx)),
    h: Math.min(videoH, Math.round(region.height * sy)),
    display: d
  };
}

function sourceIdForRegion(region) {
  const displays = (state.displays && state.displays.displays) || [];
  const d = displays.find(dd =>
    region.x >= dd.bounds.x && region.y >= dd.bounds.y &&
    region.x < dd.bounds.x + dd.bounds.width && region.y < dd.bounds.y + dd.bounds.height
  );
  const screens = state.sources.filter(s => s.type === 'screen');
  if (d) {
    const match = screens.find(s => String(s.display_id) === String(d.id));
    if (match) return match.id;
  }
  return screens.length ? screens[0].id : null;
}

/* ---------------- countdown ---------------- */
async function runCountdown() {
  const n = Number(state.settings.countdown) || 0;
  if (!n) return;
  const box = $('countdown');
  const num = $('countdownNum');
  box.classList.remove('hidden');
  for (let i = n; i > 0; i--) {
    num.textContent = i;
    await sleep(1000);
  }
  box.classList.add('hidden');
}

/* ---------------- timer ---------------- */
function startTimer() {
  state.startedAt = Date.now();
  state.elapsed = 0;
  $('timer').classList.add('live');
  state.timerId = setInterval(() => {
    if (state.paused) return;
    state.elapsed += 0.25;
    const t = fmtTime(state.elapsed);
    $('timer').textContent = t;
    window.api.updatePanel({ time: t, paused: state.paused });
  }, 250);

  const mins = Number(state.settings.autoStopMinutes) || 0;
  if (mins > 0) state.autoStopId = setTimeout(() => stopRecording(), mins * 60000);
}
function stopTimer() {
  clearInterval(state.timerId); state.timerId = null;
  clearTimeout(state.autoStopId); state.autoStopId = null;
  $('timer').classList.remove('live');
  $('timer').textContent = '00:00:00';
}

/* ---------------- record ---------------- */
$('recordBtn').onclick = () => toggleRecord();

async function toggleRecord() {
  if (state.busy) return;
  if (state.mode === 'screenshot') return takeScreenshot();
  if (state.recording) return stopRecording();
  return startRecording();
}

async function startRecording() {
  state.busy = true;
  setStatus('');
  try {
    const s = state.settings;
    const fps = Number(s.fps) || 30;
    const audioOnly = state.mode === 'audio';
    const wantMic = $('tMic').checked;
    const wantSys = $('tSys').checked;
    const wantCam = $('tCam').checked;

    if (state.mode === 'region' && !state.region) {
      const ok = await pickRegion();
      if (!ok) { state.busy = false; return; }
    }

    stopMicMeter();

    /* --- gather sources --- */
    let screenStream = null, micStream = null, camStream = null;
    let sourceId = null;               // kept out here: the composite needs it too

    if (wantMic) micStream = await getMicStream();

    if (audioOnly) {
      if (wantSys) {
        const screens = state.sources.filter(s2 => s2.type === 'screen');
        if (screens.length) {
          screenStream = await getDesktopStream(screens[0].id, true, 5);
          screenStream.getVideoTracks().forEach(t => t.stop());
        }
      }
      if (!micStream && !(screenStream && screenStream.getAudioTracks().length))
        throw new Error('Enable the microphone or system sound first.');
    } else if (state.mode === 'webcam') {
      camStream = await getCamStream();
    } else {
      sourceId = state.selectedSourceId;
      if (state.mode === 'region') sourceId = sourceIdForRegion(state.region);
      if (!sourceId) throw new Error('No capture source selected.');
      screenStream = await getDesktopStream(sourceId, wantSys, fps);
      if (wantCam) camStream = await getCamStream();
    }

    /* --- countdown --- */
    await runCountdown();

    /* --- build the video track --- */
    let videoTrack = null;
    let composited = false;

    if (!audioOnly) {
      if (state.mode === 'webcam') {
        videoTrack = camStream.getVideoTracks()[0];
      } else {
        const screenTrack = screenStream.getVideoTracks()[0];
        const needCompose = !!state.region || (wantCam && camStream);

        if (!needCompose) {
          videoTrack = screenTrack;
        } else {
          composited = true;
          const sv = document.createElement('video');
          sv.srcObject = new MediaStream([screenTrack]);
          sv.muted = true;
          await sv.play();
          await new Promise(r => (sv.readyState >= 2 ? r() : (sv.onloadeddata = r)));

          const crop = cropForRegion(state.region, sv.videoWidth, sv.videoHeight);
          const outW = crop ? crop.w : sv.videoWidth;
          const outH = crop ? crop.h : sv.videoHeight;

          const canvas = document.createElement('canvas');
          canvas.width = Math.max(2, Math.round(outW / 2) * 2);
          canvas.height = Math.max(2, Math.round(outH / 2) * 2);
          const ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });

          let cv = null;
          if (wantCam && camStream) {
            cv = document.createElement('video');
            cv.srcObject = camStream;
            cv.muted = true;
            await cv.play();
          }

          const camW = Number(s.webcamSize) || 220;
          const pad = 22;
          const draw = () => {
            if (crop) ctx.drawImage(sv, crop.x, crop.y, crop.w, crop.h, 0, 0, canvas.width, canvas.height);
            else ctx.drawImage(sv, 0, 0, canvas.width, canvas.height);

            if (cv && cv.videoWidth) {
              // Follow the floating bubble if it is on screen: it is read every
              // frame, so dragging it mid-recording moves the face in the video
              // too, and hiding it takes the face out. Only when there is no
              // bubble does the fixed corner from Settings apply.
              const live = bubbleOnCanvas(canvas, sourceId);
              if (!live && state.camBubble && state.camBubble.open && !state.camBubble.visible) return;

              const ratio = cv.videoHeight / cv.videoWidth;
              let x, y, w, h, round;
              if (live) {
                x = live.x; y = live.y; w = live.w; h = live.h;
                round = true;                       // the bubble is a circle
              } else {
                w = Math.min(camW, canvas.width * 0.4);
                h = w * ratio;
                const pos = s.webcamPosition || 'bottom-right';
                x = pos.includes('right') ? canvas.width - w - pad : pad;
                y = pos.includes('bottom') ? canvas.height - h - pad : pad;
                round = false;
              }

              ctx.save();
              ctx.beginPath();
              if (round) {
                ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
              } else {
                const r = 14;
                ctx.moveTo(x + r, y);
                ctx.arcTo(x + w, y, x + w, y + h, r);
                ctx.arcTo(x + w, y + h, x, y + h, r);
                ctx.arcTo(x, y + h, x, y, r);
                ctx.arcTo(x, y, x + w, y, r);
              }
              ctx.closePath();
              ctx.shadowColor = 'rgba(0,0,0,.55)';
              ctx.shadowBlur = 22;
              ctx.fill();
              ctx.clip();
              // cover-fit: a square bubble must not squash a 16:9 camera
              const scale = Math.max(w / cv.videoWidth, h / cv.videoHeight);
              const dw = cv.videoWidth * scale;
              const dh = cv.videoHeight * scale;
              ctx.translate(x + w / 2 + dw / 2, y + h / 2 - dh / 2);
              ctx.scale(-1, 1);            // mirror, like every webcam preview
              ctx.drawImage(cv, 0, 0, dw, dh);
              ctx.restore();
            }
          };

          rt.drawTimer = setInterval(() => { if (!state.paused) draw(); }, 1000 / fps);
          rt.canvas = canvas;
          rt.screenVideo = sv;
          rt.camVideo = cv;
          videoTrack = canvas.captureStream(fps).getVideoTracks()[0];
        }
      }
    }

    /* --- audio --- */
    const { track: audioTrack, ctx: audioCtx } = mixAudio([micStream, screenStream, camStream]);
    rt.audioCtx = audioCtx;

    const tracks = [];
    if (videoTrack) tracks.push(videoTrack);
    if (audioTrack) tracks.push(audioTrack);
    if (!tracks.length) throw new Error('Nothing to record.');

    const finalStream = new MediaStream(tracks);
    rt.streams = [screenStream, micStream, camStream].filter(Boolean);

    const bitrate = { low: 1500000, medium: 4000000, high: 8000000, lossless: 16000000 }[s.quality] || 8000000;
    const opts = audioOnly
      ? { mimeType: 'audio/webm', audioBitsPerSecond: 192000 }
      : { mimeType: pickMime(), videoBitsPerSecond: bitrate, audioBitsPerSecond: 192000 };

    let recorder;
    try { recorder = new MediaRecorder(finalStream, opts); }
    catch { recorder = new MediaRecorder(finalStream); }

    rt.recorder = recorder;
    rt.chunks = [];
    recorder.ondataavailable = e => { if (e.data && e.data.size) rt.chunks.push(e.data); };
    recorder.onstop = () => finalize(audioOnly);

    // the user closing the shared surface stops us too
    if (videoTrack) videoTrack.onended = () => { if (state.recording) stopRecording(); };

    recorder.start(1000);
    state.recording = true;
    state.paused = false;
    startTimer();

    $('recordBtn').classList.add('recording');
    $('recordLabel').textContent = 'Stop recording';
    setStatus('Recording' + (composited ? ' (composited)' : '') + '...');

    window.api.openPanel({ time: '00:00:00', paused: false });
    if (s.minimizeOnRecord === true || s.minimizeOnRecord === 'true') window.api.hideWindow();
  } catch (e) {
    setStatus(e.message || String(e), 'err');
    cleanup();
  } finally {
    state.busy = false;
  }
}

function pauseRecording() {
  if (!state.recording || !rt.recorder) return;
  if (state.paused) {
    rt.recorder.resume();
    state.paused = false;
    setStatus('Recording...');
  } else {
    rt.recorder.pause();
    state.paused = true;
    setStatus('Paused.');
  }
  window.api.updatePanel({ time: fmtTime(state.elapsed), paused: state.paused });
}

function stopRecording() {
  if (!state.recording || !rt.recorder) return;
  state.recording = false;
  state.paused = false;
  stopTimer();
  window.api.closePanel();
  window.api.showWindow();
  $('recordBtn').classList.remove('recording');
  $('recordLabel').textContent = state.mode === 'audio' ? 'Start audio recording' : 'Start recording';
  setStatus('Saving...');
  try { rt.recorder.stop(); } catch { cleanup(); }
}

async function finalize(audioOnly) {
  try {
    const blob = new Blob(rt.chunks, { type: audioOnly ? 'audio/webm' : 'video/webm' });
    rt.chunks = [];
    cleanup();
    if (!blob.size) { setStatus('Nothing was captured.', 'err'); return; }

    setStatus('Processing ' + fmtSize(blob.size) + '...');
    const buf = await blob.arrayBuffer();
    const res = await window.api.saveRecording({
      buffer: new Uint8Array(buf),
      ext: 'webm',
      audioOnly,
      baseName: audioOnly ? 'Audio' : (state.mode === 'webcam' ? 'Webcam' : 'Recording')
    });
    setStatus('Saved: ' + res.path + (res.warning ? '  (' + res.warning + ')' : ''), res.warning ? 'err' : 'ok');
    loadLibrary();
  } catch (e) {
    setStatus('Save failed: ' + e.message, 'err');
    cleanup();
  }
}

function cleanup() {
  if (rt.drawTimer) { clearInterval(rt.drawTimer); rt.drawTimer = null; }
  if (rt.raf) { cancelAnimationFrame(rt.raf); rt.raf = null; }
  rt.streams.forEach(s => { try { s.getTracks().forEach(t => t.stop()); } catch {} });
  rt.streams = [];
  if (rt.audioCtx) { rt.audioCtx.close().catch(() => {}); rt.audioCtx = null; }
  [rt.screenVideo, rt.camVideo].forEach(v => { if (v) { try { v.pause(); v.srcObject = null; } catch {} } });
  rt.screenVideo = rt.camVideo = rt.canvas = rt.recorder = null;
  if ($('tCam').checked) onCamToggle();
  if ($('tMic').checked) startMicMeter();
}

/* ---------------- screenshot ---------------- */
async function takeScreenshot() {
  state.busy = true;
  try {
    setStatus('Select an area...');
    const r = await window.api.selectRegion('screenshot');
    if (!r) { setStatus('Cancelled.'); state.busy = false; return; }
    const region = r.fullscreen ? null : r;
    await sleep(180);

    state.displays = await window.api.getDisplays();
    state.sources = await window.api.listSources();
    const sourceId = region ? sourceIdForRegion(region) : (state.sources.find(s => s.type === 'screen') || {}).id;
    if (!sourceId) throw new Error('No display found.');

    const stream = await getDesktopStream(sourceId, false, 30);
    const v = document.createElement('video');
    v.srcObject = stream;
    v.muted = true;
    await v.play();
    await new Promise(res => (v.readyState >= 2 ? res() : (v.onloadeddata = res)));
    await sleep(120);

    const crop = cropForRegion(region, v.videoWidth, v.videoHeight);
    const canvas = document.createElement('canvas');
    canvas.width = crop ? crop.w : v.videoWidth;
    canvas.height = crop ? crop.h : v.videoHeight;
    const ctx = canvas.getContext('2d');
    if (crop) ctx.drawImage(v, crop.x, crop.y, crop.w, crop.h, 0, 0, canvas.width, canvas.height);
    else ctx.drawImage(v, 0, 0, canvas.width, canvas.height);

    stream.getTracks().forEach(t => t.stop());
    v.srcObject = null;

    const blob = await new Promise(res => canvas.toBlob(res, 'image/png'));
    const buf = await blob.arrayBuffer();
    const out = await window.api.saveScreenshot({ buffer: new Uint8Array(buf) });
    setStatus('Screenshot saved: ' + out.path, 'ok');
    loadLibrary();
  } catch (e) {
    setStatus('Screenshot failed: ' + e.message, 'err');
  } finally {
    state.busy = false;
  }
}

/* ---------------- library ---------------- */
async function loadLibrary() {
  const items = await window.api.listLibrary();
  const grid = $('libGrid');
  $('libPath').textContent = state.settings ? state.settings.outputDir : '';
  grid.innerHTML = '';
  if (!items.length) {
    grid.innerHTML = '<div class="empty">No recordings yet.</div>';
    return;
  }
  items.forEach(it => {
    const isImg = it.ext === '.png' || it.ext === '.jpg' || it.ext === '.gif';
    const isAudio = it.ext === '.m4a' || it.ext === '.mp3';
    const url = 'file:///' + it.path.replace(/\\/g, '/').replace(/#/g, '%23');

    const card = document.createElement('div');
    card.className = 'lib-item';
    card.innerHTML =
      '<div class="lib-thumb">' +
        (isImg ? '<img src="' + url + '" />' :
         isAudio ? '<span>&#9834;</span>' :
         '<video src="' + url + '" muted preload="metadata"></video>') +
      '</div>' +
      '<div class="lib-meta"><div class="lib-name"></div><div class="lib-sub"></div></div>' +
      '<div class="lib-btns">' +
        '<button class="ghost small" data-a="open">Play</button>' +
        '<button class="ghost small" data-a="reveal">Folder</button>' +
        '<button class="ghost small danger" data-a="del">Delete</button>' +
      '</div>';
    card.querySelector('.lib-name').textContent = it.name;
    card.querySelector('.lib-sub').textContent =
      fmtSize(it.size) + '  ·  ' + new Date(it.mtime).toLocaleString();
    card.querySelector('.lib-thumb').onclick = () => window.api.openFile(it.path);
    card.querySelectorAll('button').forEach(b => {
      b.onclick = async () => {
        const a = b.dataset.a;
        if (a === 'open') window.api.openFile(it.path);
        if (a === 'reveal') window.api.revealFile(it.path);
        if (a === 'del') { await window.api.deleteFile(it.path); loadLibrary(); }
      };
    });
    grid.appendChild(card);
  });
}
$('libRefresh').onclick = loadLibrary;
$('libFolder').onclick = () => window.api.openFolder();

/* ---------------- settings ---------------- */
function bindSettings() {
  const s = state.settings;
  $('sOutDir').value = s.outputDir;
  $('sFormat').value = s.format;
  $('sQuality').value = s.quality;
  $('sFps').value = String(s.fps);
  $('sCountdown').value = String(s.countdown);
  $('sAutoStop').value = String(s.autoStopMinutes || 0);
  $('sCamSize').value = String(s.webcamSize);
  $('sCamPos').value = s.webcamPosition;
  $('sMinimize').value = String(s.minimizeOnRecord);
  $('sHkStart').value = s.hotkeys.startStop;
  $('sHkPause').value = s.hotkeys.pause;
  $('sHkShot').value = s.hotkeys.screenshot;
  $('sHkCam').value = s.hotkeys.camera || 'F8';

  $('tMic').checked = !!s.micEnabled;
  $('tSys').checked = !!s.systemAudio;
  $('tCam').checked = !!s.webcamEnabled;
  $('camDevice').disabled = !s.webcamEnabled;
  refreshChips();
}

function refreshChips() {
  const s = state.settings;
  $('chipFormat').textContent = s.format.toUpperCase();
  $('chipQuality').textContent = s.quality.charAt(0).toUpperCase() + s.quality.slice(1);
  $('chipFps').textContent = s.fps + ' fps';
  $('hkStart').textContent = s.hotkeys.startStop;
  $('hkPause').textContent = s.hotkeys.pause;
  $('hkShot').textContent = s.hotkeys.screenshot;
  $('hkCam').textContent = s.hotkeys.camera || 'F8';
}

async function save(patch) {
  state.settings = await window.api.setSettings(patch);
  refreshChips();
}

$('sPickDir').onclick = async () => {
  const dir = await window.api.pickDir();
  state.settings.outputDir = dir;
  $('sOutDir').value = dir;
  loadLibrary();
};
$('sFormat').onchange = e => save({ format: e.target.value });
$('sQuality').onchange = e => save({ quality: e.target.value });
$('sFps').onchange = e => save({ fps: Number(e.target.value) });
$('sCountdown').onchange = e => save({ countdown: Number(e.target.value) });
$('sAutoStop').onchange = e => save({ autoStopMinutes: Number(e.target.value) });
$('sCamSize').onchange = e => save({ webcamSize: Number(e.target.value) });
$('sCamPos').onchange = e => save({ webcamPosition: e.target.value });
$('sMinimize').onchange = e => save({ minimizeOnRecord: e.target.value === 'true' });

$('tMic').onchange = e => { save({ micEnabled: e.target.checked }); $('micDevice').disabled = !e.target.checked; startMicMeter(); };
$('tSys').onchange = e => save({ systemAudio: e.target.checked });
$('tCam').onchange = e => { save({ webcamEnabled: e.target.checked }); onCamToggle(); };
$('camDevice').onchange = () => onCamToggle();
$('micDevice').onchange = () => startMicMeter();

/* hotkey capture */
function accelFrom(e) {
  const parts = [];
  if (e.ctrlKey) parts.push('Control');
  if (e.altKey) parts.push('Alt');
  if (e.shiftKey) parts.push('Shift');
  if (e.metaKey) parts.push('Super');
  let key = e.key;
  if (['Control', 'Alt', 'Shift', 'Meta'].includes(key)) return null;
  if (key === ' ') key = 'Space';
  else if (key.length === 1) key = key.toUpperCase();
  parts.push(key);
  return parts.join('+');
}
[['sHkStart', 'startStop'], ['sHkPause', 'pause'], ['sHkShot', 'screenshot'], ['sHkCam', 'camera']].forEach(([id, key]) => {
  const el = $(id);
  el.onclick = () => {
    el.classList.add('listening');
    el.value = 'Press keys...';
    const handler = e => {
      e.preventDefault();
      const acc = accelFrom(e);
      if (!acc) return;
      window.removeEventListener('keydown', handler, true);
      el.classList.remove('listening');
      el.value = acc;
      save({ hotkeys: { [key]: acc } });
    };
    window.addEventListener('keydown', handler, true);
  };
});

/* ---------------- global events ---------------- */
window.api.onHotkey(k => {
  if (k === 'startStop') toggleRecord();
  else if (k === 'pause') pauseRecording();
  else if (k === 'screenshot') takeScreenshot();
});
window.api.onPanelAction(a => {
  if (a === 'stop') stopRecording();
  if (a === 'pause') pauseRecording();
});
window.api.onConvertProgress(sec => setStatus('Converting... ' + fmtTime(sec) + ' encoded'));

window.addEventListener('beforeunload', () => { window.api.closePanel(); cleanup(); });

/* ---------------- boot ---------------- */
(async function init() {
  state.settings = await window.api.getSettings();
  bindSettings();
  await loadSources();
  await loadDevices();
  applyMode();
  if ($('tMic').checked) startMicMeter();
  if ($('tCam').checked) onCamToggle();
  loadLibrary();

  const ok = await window.api.checkFfmpeg();
  $('ffmpegBadge').classList.toggle('hidden', ok);
  if (!ok) setStatus('ffmpeg not found - recordings will be saved as WebM. Install ffmpeg for MP4/GIF.', 'err');
})();
