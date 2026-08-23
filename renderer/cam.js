/* The floating camera bubble.

   Drag comes free from -webkit-app-region on the bubble and resize from the
   window's own edges, so the only work here is holding the camera stream and
   surviving a device change without leaking the old one. */

const video = document.getElementById('v');
const msg = document.getElementById('msg');
const bubble = document.getElementById('bubble');

let stream = null;

function say(text) {
  msg.textContent = text;
  msg.classList.toggle('on', !!text);
}

async function start(deviceId) {
  stop();
  say('');
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: deviceId ? { deviceId: { exact: deviceId } } : true,
      audio: false
    });
    video.srcObject = stream;
  } catch (e) {
    // the bubble is the only place this is visible once the app is minimised
    say(e && e.name === 'NotAllowedError' ? 'Camera blocked' : 'No camera');
  }
}

function stop() {
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
  video.srcObject = null;
}

window.api.onCamDevice(id => start(id || ''));

document.getElementById('hide').onclick = () => window.api.setCamVisible(false);
document.getElementById('shape').onclick = () => bubble.classList.toggle('square');

window.addEventListener('beforeunload', stop);

start('');
