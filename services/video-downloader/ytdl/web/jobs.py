"""
Job tracking for the web UI.

yt-dlp is blocking and reports progress through callbacks, so each job runs
on its own thread and pushes events into a queue the HTTP layer drains. The
registry is in memory only -- this is a single-user local tool, and a
restart is a perfectly good way to clear the list.
"""

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..downloader import build_options

# Finished jobs stay in the registry so the page can be reloaded without
# losing the list. Oldest are dropped past this many.
MAX_JOBS = 200


@dataclass
class Job:
    id: str
    urls: list[str]
    options: dict
    status: str = "queued"          # queued | running | done | error | cancelled
    title: str = ""
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    stage: str = ""                 # downloading | merging | converting ...
    error: str = ""
    thumbnail: str = ""
    duration: int = 0
    files: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    _cancel: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict[str, Any]:
        """The shape the browser consumes. Underscore fields stay private."""
        return {
            "id": self.id,
            "urls": self.urls,
            "status": self.status,
            "title": self.title or self.urls[0],
            "percent": round(self.percent, 1),
            "speed": self.speed,
            "eta": self.eta,
            "stage": self.stage,
            "error": self.error,
            "thumbnail": self.thumbnail,
            "duration": self.duration,
            "files": self.files,
            "created": self.created,
        }


class Cancelled(Exception):
    """Raised inside a progress hook to unwind yt-dlp on user request."""


class JobRegistry:
    """Thread-safe store of jobs plus a fan-out queue of change events."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._listeners: list[queue.Queue] = []

    # -- registry ---------------------------------------------------------

    def all(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)
            return [j.snapshot() for j in jobs]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            if len(self._jobs) > MAX_JOBS:
                for old in sorted(self._jobs.values(), key=lambda j: j.created)[:20]:
                    if old.status in ("done", "error", "cancelled"):
                        self._jobs.pop(old.id, None)

    def clear_finished(self) -> int:
        with self._lock:
            gone = [j.id for j in self._jobs.values()
                    if j.status in ("done", "error", "cancelled")]
            for jid in gone:
                del self._jobs[jid]
        return len(gone)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job and job.status in ("queued", "running"):
            job._cancel.set()
            return True
        return False

    # -- event fan-out ----------------------------------------------------

    def listen(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._listeners.append(q)
        return q

    def unlisten(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def _publish(self, job: Job) -> None:
        snap = job.snapshot()
        with self._lock:
            listeners = list(self._listeners)
        for q in listeners:
            try:
                q.put_nowait(snap)
            except queue.Full:
                # A browser tab that stopped reading shouldn't stall the
                # download thread; it will resync on its next full fetch.
                pass

    # -- running ----------------------------------------------------------

    def submit(self, urls: list[str], out_dir: Path, **opts) -> Job:
        options = build_options(out_dir=out_dir, **opts)
        job = Job(id=uuid.uuid4().hex[:12], urls=urls, options=options)
        self._add(job)
        self._publish(job)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        import yt_dlp

        def hook(d: dict) -> None:
            if job._cancel.is_set():
                raise Cancelled()
            info = d.get("info_dict") or {}
            if title := info.get("title"):
                job.title = title
            if thumb := info.get("thumbnail"):
                job.thumbnail = thumb
            if dur := info.get("duration"):
                job.duration = int(dur)
            status = d.get("status")
            if status == "downloading":
                job.stage = "downloading"
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                job.percent = (done / total * 100) if total else 0.0
                job.speed = _human_speed(d.get("speed"))
                job.eta = _human_eta(d.get("eta"))
            elif status == "finished":
                # Download done, but postprocessing (merge/convert) follows.
                job.percent = 100.0
                job.speed = ""
                job.eta = ""
                job.stage = "processing"
                if path := d.get("filename"):
                    name = Path(path).name
                    if name not in job.files:
                        job.files.append(name)
            self._publish(job)

        def pp_hook(d: dict) -> None:
            status = d.get("status")
            if status == "started":
                job.stage = (d.get("postprocessor") or "processing").lower()
            elif status == "finished":
                # The download hook only ever sees the pre-processing name:
                # an mp3 rip reports the .webm that gets deleted straight
                # after. Trust the postprocessor for the name on disk.
                info = d.get("info_dict") or {}
                final = info.get("filepath") or info.get("_filename")
                if final:
                    job.files = [Path(final).name]
            self._publish(job)

        options = dict(job.options)
        options["progress_hooks"] = [hook]
        options["postprocessor_hooks"] = [pp_hook]
        options["quiet"] = True
        options["no_warnings"] = True
        options["noprogress"] = True

        job.status = "running"
        self._publish(job)
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                code = ydl.download(job.urls)
            if job._cancel.is_set():
                job.status = "cancelled"
            elif code == 0:
                job.status = "done"
                job.stage = ""
                job.percent = 100.0
            else:
                job.status = "error"
                job.error = "yt-dlp reported one or more failures."
        except Cancelled:
            job.status = "cancelled"
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            job.status = "cancelled" if job._cancel.is_set() else "error"
            if job.status == "error":
                job.error = str(exc)
        finally:
            job.speed = job.eta = ""
            self._publish(job)


def _human_speed(speed: float | None) -> str:
    if not speed:
        return ""
    for unit in ("B/s", "KiB/s", "MiB/s"):
        if speed < 1024 or unit == "MiB/s":
            return f"{speed:.1f} {unit}"
        speed /= 1024
    return ""


def _human_eta(eta: int | None) -> str:
    if eta is None:
        return ""
    m, s = divmod(int(eta), 60)
    return f"{m}:{s:02d}" if m else f"{s}s"


registry = JobRegistry()
