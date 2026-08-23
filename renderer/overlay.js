(function () {
  const sel = document.getElementById('sel');
  const sizeTag = document.getElementById('size');
  const hint = document.getElementById('hint');
  let startX = 0, startY = 0, dragging = false, rect = null;
  let origin = { x: 0, y: 0 };

  window.api.getDisplays().then(d => { origin = d.origin; });

  function draw(x1, y1, x2, y2) {
    const x = Math.min(x1, x2), y = Math.min(y1, y2);
    const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    sel.style.display = 'block';
    sel.style.left = x + 'px';
    sel.style.top = y + 'px';
    sel.style.width = w + 'px';
    sel.style.height = h + 'px';
    sizeTag.style.display = 'block';
    sizeTag.style.left = x + 'px';
    sizeTag.style.top = y + 'px';
    sizeTag.textContent = Math.round(w) + ' x ' + Math.round(h);
    return { x, y, width: w, height: h };
  }

  window.addEventListener('mousedown', e => {
    dragging = true;
    startX = e.clientX; startY = e.clientY;
    hint.style.display = 'none';
  });

  window.addEventListener('mousemove', e => {
    if (!dragging) return;
    rect = draw(startX, startY, e.clientX, e.clientY);
  });

  window.addEventListener('mouseup', e => {
    if (!dragging) return;
    dragging = false;
    rect = draw(startX, startY, e.clientX, e.clientY);
    if (rect.width < 12 || rect.height < 12) {
      sel.style.display = 'none';
      sizeTag.style.display = 'none';
      hint.style.display = 'block';
      return;
    }
    // even dimensions keep H.264 encoders happy
    const w = Math.max(16, Math.round(rect.width / 2) * 2);
    const h = Math.max(16, Math.round(rect.height / 2) * 2);
    window.api.sendRegion({
      x: Math.round(rect.x) + origin.x,
      y: Math.round(rect.y) + origin.y,
      width: w,
      height: h
    });
  });

  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') window.api.sendRegion(null);
    if (e.key === 'Enter') window.api.sendRegion({ fullscreen: true });
  });
})();
