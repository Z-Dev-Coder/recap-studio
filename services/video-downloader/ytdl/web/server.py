"""
FastAPI app behind the local web UI.

Binds to localhost by default: this exposes a "download anything and write
it to disk" endpoint with no authentication, which is fine for one machine
and is not something to put on a network without thinking about it.
"""

import asyncio
import json
import os
import queue
import subprocess
import sys
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..downloader import default_out_dir
from .jobs import registry
from .recap_api import router as recap_router

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Video Downloader", docs_url=None, redoc_url=None)
app.include_router(recap_router)


class DownloadRequest(BaseModel):
    """Mirrors the CLI flags one-for-one, so the UI is never less capable."""

    urls: list[str] = Field(min_length=1)
    quality: str = "1080"
    audio: bool = False
    subs: bool = False
    playlist: bool = False
    keep: bool = False
    cookies_browser: str = ""
    out: str = ""


# The UI is served from disk and updated in place by app updates, so a cached
# copy is always the wrong copy -- and a stale page is indistinguishable from a
# broken one to whoever is looking at it.
NO_CACHE = {"Cache-Control": "no-store, must-revalidate"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers=NO_CACHE)


@app.get("/recap")
def recap_page() -> FileResponse:
    return FileResponse(STATIC / "recap.html", headers=NO_CACHE)


@app.get("/api/config")
def config() -> dict:
    return {"default_out": str(default_out_dir())}


@app.get("/api/jobs")
def jobs() -> list[dict]:
    return registry.all()


@app.post("/api/download")
def start_download(req: DownloadRequest) -> dict:
    if req.quality != "best" and not req.quality.isdigit():
        raise HTTPException(400, "quality must be a number (e.g. 1080) or 'best'")

    urls = [u.strip() for u in req.urls if u.strip()]
    if not urls:
        raise HTTPException(400, "no URLs given")

    out_dir = Path(req.out) if req.out.strip() else default_out_dir()
    try:
        job = registry.submit(
            urls,
            out_dir=out_dir,
            quality=req.quality,
            audio_only=req.audio,
            subs=req.subs,
            playlist=req.playlist,
            cookies_browser=req.cookies_browser,
            keep_originals=req.keep,
        )
    except OSError as exc:
        raise HTTPException(400, f"cannot write to {out_dir}: {exc}") from exc
    return job.snapshot()


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str) -> dict:
    if not registry.cancel(job_id):
        raise HTTPException(404, "no such job, or it already finished")
    return {"ok": True}


@app.get("/api/info")
def info(url: str, cookies_browser: str = "") -> dict:
    """
    Cheap metadata peek so the UI can show what you are about to download.

    process=False skips format extraction, which is the slow part -- title,
    thumbnail and duration all survive without it.
    """
    import yt_dlp

    from ..downloader import _js_runtimes

    options = {
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": _js_runtimes(),
        "noplaylist": False,
        "extract_flat": "in_playlist",
    }
    if cookies_browser:
        options["cookiesfrombrowser"] = (cookies_browser,)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            data = ydl.extract_info(url, download=False, process=False)
    except Exception as exc:  # noqa: BLE001 - surfaced as a hint, not fatal
        raise HTTPException(400, str(exc)) from exc

    entries = data.get("entries")
    is_playlist = data.get("_type") == "playlist" or entries is not None
    count = data.get("playlist_count") or 0
    if is_playlist and not count and isinstance(entries, list):
        count = len(entries)

    return {
        "title": data.get("title") or "",
        "uploader": data.get("uploader") or data.get("channel") or "",
        "duration": int(data.get("duration") or 0),
        "thumbnail": data.get("thumbnail") or "",
        "is_playlist": bool(is_playlist),
        "count": count,
    }


@app.post("/api/jobs/clear")
def clear_finished() -> dict:
    return {"cleared": registry.clear_finished()}


@app.get("/api/formats")
def formats(url: str, cookies_browser: str = "") -> dict:
    """The -f flag: what's actually available, without downloading."""
    import yt_dlp

    from ..downloader import _js_runtimes

    options = {
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": _js_runtimes(),
        "noplaylist": True,
    }
    if cookies_browser:
        options["cookiesfrombrowser"] = (cookies_browser,)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 - shown to the user verbatim
        raise HTTPException(400, str(exc)) from exc

    rows = []
    for f in info.get("formats") or []:
        rows.append({
            "id": f.get("format_id"),
            "ext": f.get("ext"),
            "resolution": f.get("resolution") or "audio only",
            "fps": f.get("fps"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "vcodec": f.get("vcodec"),
            "acodec": f.get("acodec"),
            "note": f.get("format_note") or "",
        })
    return {"title": info.get("title", ""), "formats": rows}


@app.post("/api/reveal")
def reveal(payload: dict) -> dict:
    """Open the output folder in Explorer, selecting the file when given."""
    folder = Path(payload.get("folder") or default_out_dir())
    name = (payload.get("file") or "").strip()
    target = folder / name if name else folder
    if not target.exists():
        target = folder
    if not target.exists():
        raise HTTPException(404, f"{folder} does not exist")
    if sys.platform == "win32":
        if target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            os.startfile(target)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return {"ok": True}


@app.get("/api/events")
async def events() -> StreamingResponse:
    """Server-sent events: one message per job state change."""
    q = registry.listen()

    async def stream():
        loop = asyncio.get_running_loop()
        try:
            # Prime the connection so a reconnecting tab is immediately correct.
            for snap in registry.all():
                yield f"data: {json.dumps(snap)}\n\n"
            while True:
                try:
                    snap = await loop.run_in_executor(None, q.get, True, 20)
                except queue.Empty:
                    yield ": keepalive\n\n"   # keeps proxies and the tab awake
                    continue
                yield f"data: {json.dumps(snap)}\n\n"
        finally:
            registry.unlisten(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def run(host: str = "127.0.0.1", port: int = 8756, open_browser: bool = True) -> int:
    import uvicorn

    url = f"http://{host}:{port}/"
    print(f"Video Downloader UI: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        # Fires once the server is up; a moment early just retries in the tab.
        import threading
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
