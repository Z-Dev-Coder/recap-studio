from __future__ import annotations

import threading
from typing import Callable
from .project import Project, Store
from . import pipeline
from . import localtts as localtts_mod
from .media import Cancelled

class RecapPipeline:
    """
    Orchestrates the execution of recap stages.
    Maintains robust state machines and error handling as per Phase 1 requirements.
    """

    def __init__(self, store: Store):
        self.store = store
        self._running: dict[str, str] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def get_cancel_event(self, pid: str) -> threading.Event:
        with self._lock:
            if pid not in self._cancels:
                self._cancels[pid] = threading.Event()
            return self._cancels[pid]

    def _claim(self, pid: str, step: str) -> bool:
        with self._lock:
            if pid in self._running:
                return False
            self._running[pid] = step
            self._cancels.setdefault(pid, threading.Event()).clear()
            return True

    def _release(self, pid: str) -> None:
        with self._lock:
            self._running.pop(pid, None)

    def cancel(self, pid: str) -> str | None:
        self.get_cancel_event(pid).set()
        with self._lock:
            return self._running.get(pid)

    def reconcile(self, project: Project) -> bool:
        with self._lock:
            live = self._running.get(project.id)
        changed = False
        for name, step in (project.steps or {}).items():
            if getattr(step, "status", "") == "running" and name != live:
                project.mark(name, "idle", message="interrupted -- run it again")
                changed = True
        if changed:
            project.save()
        return changed

    def run_step(
        self, 
        project: Project, 
        step: str, 
        options: dict, 
        on_update: Callable[[Project], None],
        release: bool = True
    ) -> None:
        """Executes a single step, updating project state and notifying listeners."""
        stop = self.get_cancel_event(project.id)
        project.mark(step, "running", message="working...")
        on_update(project)

        try:
            # Delegate to existing pipeline logic for now
            if step == "source":
                pipeline.run_source(
                    project,
                    cookies_browser=options.get("cookies_browser", ""),
                    quality=str(options.get("quality", "1080")),
                    cancel=stop
                )
            elif step == "transcript":
                pipeline.run_transcript(
                    project,
                    cookies_browser=options.get("cookies_browser", ""),
                    whisper_model=options.get("whisper_model", "small"),
                    cancel=stop,
                    api_key=options.get("api_key", ""),
                    model=options.get("gemini_model", ""),
                    watch_only=bool(options.get("watch", False)),
                    on_progress=lambda d, t: on_update(project),
                    stage_models=options.get("stage_models", {})
                )
            elif step == "script":
                project.script_by_hand = False
                def script_prog(pct):
                    project.mark("script", "running", progress=pct, message="working...")
                    on_update(project)
                pipeline.run_script(
                    project,
                    api_key=options.get("api_key", ""),
                    model=options.get("gemini_model", ""),
                    temperature=float(options.get("temperature", 0.7)),
                    use_scrape=bool(options.get("use_scrape", True)),
                    use_vision=bool(options.get("use_vision", True)),
                    cancel=stop,
                    light_model=options.get("light_model", ""),
                    light_analysis=bool(options.get("light_analysis", False)),
                    quality=options.get("quality", ""),
                    stage_models=options.get("stage_models", {}),
                    keys={"gemini": options.get("api_key", ""), "groq": options.get("groq_key", "")},
                    ollama_url=options.get("ollama_url", ""),
                    on_progress=script_prog
                )
            elif step == "script_qa":
                # Explicit Script QA stage
                def qa_update(done, total):
                    project.mark("script_qa", "running", progress=done/max(1, total), message=f"Reviewing {done} of {total} lines")
                    on_update(project)
                pipeline.run_script_qa(project, api_key=options.get("api_key", ""), cancel=stop, on_progress=qa_update)
            elif step == "video":
                def progress(done: int, total: int):
                    project.mark("video", "running", progress=done/max(1, total), message=f"clip {done} of {total}")
                    on_update(project)
                pipeline.run_video(project, on_progress=progress, cancel=stop)
            elif step == "voice":
                def voiced(done: int, total: int):
                    project.mark("voice", "running", progress=done/max(1, total), message=f"line {done} of {total}")
                    on_update(project)
                pipeline.run_voice(
                    project,
                    api_key=options.get("api_key", ""),
                    timesteps=int(options.get("voice_timesteps", 0)),
                    cancel=stop,
                    on_progress=voiced
                )
            elif step == "thumbnail":
                pipeline.run_thumbnail(project, count=int(options.get("frame_count", 16)), cancel=stop)
            elif step == "final":
                def rendered(done: int, total: int, part: float | None = None):
                    at = done + (part or 0.0)
                    project.mark("final", "running", progress=at/max(1, total), message=f"rendering {min(done+1, total)} of {total}")
                    on_update(project)
                pipeline.run_final(project, on_progress=rendered, cancel=stop)
            elif step == "qc":
                # Final Output QC stage
                pipeline.run_qc(project, cancel=stop)
            else:
                raise ValueError(f"unknown step: {step}")

            # cleanup post-run

            note = project.steps.get(step)
            keep = (note.message or "") if note else ""
            project.mark(step, "done", message=keep if step == "video" else "")
            
        except Cancelled:
            project.mark(step, "idle", message="stopped")
        except Exception as exc:
            if stop.is_set():
                project.mark(step, "idle", message="stopped")
            else:
                project.mark(step, "error", error=str(exc) or exc.__class__.__name__)
        finally:
            if step == "voice":
                try:
                    from .services import get_tts_service
                    get_tts_service("voxcpm").release()
                except Exception:
                    pass
            if release:
                self._release(project.id)
            on_update(project)

    def run_chain(self, pid: str, steps: list[str], options: dict, on_update: Callable[[Project], None]) -> None:
        if not self._claim(pid, "chain"):
            raise RuntimeError("this project already has a step running")
        
        project = self.store.get(pid)
        if not project:
            self._release(pid)
            return

        def worker():
            try:
                stop = self.get_cancel_event(pid)
                for i, step in enumerate(steps):
                    if stop.is_set():
                        break
                    
                    p = self.store.get(pid)
                    # Support caption burn interruption
                    if step == "final" and p and p.snapshot().get("needs_caption_burn"):
                        p.chain_pending = list(steps[i:])
                        p.mark("final", "idle", message="waiting for Burmese captions -- finishes manually")
                        p.save()
                        on_update(p)
                        break
                    
                    self.run_step(p, step, options, on_update, release=False)
                    p = self.store.get(pid)
                    if not p or p.steps[step].status == "error":
                        break
            finally:
                self._release(pid)

        threading.Thread(target=worker, daemon=True).start()
