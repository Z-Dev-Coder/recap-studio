const dot = document.getElementById('dot');
const time = document.getElementById('time');
const pauseBtn = document.getElementById('pause');

const camBtn = document.getElementById('cam');

/* Show/hide the floating camera bubble mid-recording. The bubble can hide
   itself, but once the main window is minimised this is the only way back. */
function paintCam(st) {
  const on = !!(st && st.open);
  camBtn.hidden = !on;
  if (on) camBtn.textContent = st.visible ? 'Hide cam' : 'Show cam';
}
camBtn.onclick = async () => {
  const st = await window.api.getCamState();
  paintCam(await window.api.setCamVisible(!(st && st.visible)));
};
window.api.onCamState(paintCam);
window.api.getCamState().then(paintCam);

document.getElementById('stop').onclick = () => window.api.panelAction('stop');
pauseBtn.onclick = () => window.api.panelAction('pause');

window.api.onPanelState(s => {
  if (!s) return;
  if (typeof s.time === 'string') time.textContent = s.time;
  if (typeof s.paused === 'boolean') {
    dot.classList.toggle('paused', s.paused);
    pauseBtn.textContent = s.paused ? 'Resume' : 'Pause';
  }
});
