"""
HTTP surface for Recap Studio.

Steps run on background threads and report through the same server-sent
events the download list already uses, so the page stays live without
polling. Every generated field is also writable, because the whole point is
that the model's first attempt is a draft the user edits.
"""

from __future__ import annotations

import base64
import json
import queue
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

from ..downloader import default_out_dir
from ..recap import pipeline
from ..recap.gemini import MAX_OUTPUT_TOKENS, Gemini, GeminiError
from ..recap import media as media_mod
from ..recap.media import Cancelled, MediaError, have_ffmpeg, to_wav
from ..recap.project import STEPS, Store
from ..recap.scrape import available as playwright_available
from ..recap.scrape import install_hint as playwright_hint
from ..recap import script as script_mod
from ..recap.transcript import Cue, to_srt, whisper_available
from ..recap import localtts as localtts_mod
from ..recap import tts as tts_mod
from ..recap.video import recap_srt, source_srt

router = APIRouter(prefix="/api/recap", tags=["recap"])

ROOT = Path(default_out_dir()) / "RecapStudio"
store = Store(ROOT)

SETTINGS_FILE = ROOT / "settings.json"
_settings_lock = threading.Lock()


# ---------------------------------------------------------------- settings

def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(patch: dict) -> dict:
    with _settings_lock:
        data = load_settings()
        data.update({k: v for k, v in patch.items() if v is not None})
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data


# ------------------------------------------------------------------ events

_listeners: list[queue.Queue] = []
_listeners_lock = threading.Lock()


def broadcast(payload: dict) -> None:
    with _listeners_lock:
        targets = list(_listeners)
    for q in targets:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass


def push(project) -> None:
    broadcast(project.snapshot())


# ------------------------------------------------------------------ running

_running: dict[str, str] = {}                    # project id -> step running
_cancels: dict[str, threading.Event] = {}        # project id -> stop switch
_running_lock = threading.Lock()


def cancel_event(pid: str) -> threading.Event:
    """The stop switch for a project, created on first use."""
    with _running_lock:
        ev = _cancels.get(pid)
        if ev is None:
            ev = _cancels[pid] = threading.Event()
        return ev


def _claim(pid: str, step: str) -> bool:
    with _running_lock:
        if pid in _running:
            return False
        _running[pid] = step
        # a fresh run must not inherit the stop from the previous one
        _cancels.setdefault(pid, threading.Event()).clear()
        return True


def _release(pid: str) -> None:
    with _running_lock:
        _running.pop(pid, None)


def run_step(pid: str, step: str, options: dict, release: bool = True) -> None:
    """
    Run one step on a worker thread, reporting state as it goes.

    `release` is False when a chain is driving: the chain holds a single claim
    across all of its steps, so releasing after each one would let a second
    run start halfway through the first.
    """
    project = store.get(pid)
    if not project:
        return

    settings = load_settings()
    stop = cancel_event(pid)
    project.mark(step, "running", message="working...")
    push(project)

    try:
        if step == "source":
            pipeline.run_source(
                project,
                cookies_browser=options.get("cookies_browser", ""),
                quality=str(options.get("quality", "1080")),
                cancel=stop,
            )
        elif step == "transcript":
            pipeline.run_transcript(
                project,
                cookies_browser=options.get("cookies_browser", ""),
                whisper_model=settings.get("whisper_model", "small"),
                cancel=stop,
            )
        elif step == "script":
            key = options.get("api_key") or settings.get("gemini_key", "")
            model = settings.get("gemini_model", "") or _auto_model(key)
            if model and model != settings.get("gemini_model"):
                save_settings({"gemini_model": model})   # remember what worked
            pipeline.run_script(
                project,
                api_key=key,
                model=model,
                temperature=float(options.get("temperature", 0.7)),
                use_scrape=bool(options.get("use_scrape", settings.get("use_scrape", True))),
                use_vision=bool(options.get("use_vision", settings.get("use_vision", True))),
            )
        elif step == "video":
            def progress(done: int, total: int) -> None:
                project.mark("video", "running", message=f"clip {done} of {total}")
                push(project)

            pipeline.run_video(project, on_progress=progress, cancel=stop)
        elif step == "voice":
            key = options.get("api_key") or settings.get("gemini_key", "")

            def voiced(done: int, total: int) -> None:
                project.mark("voice", "running", message=f"line {done} of {total}")
                push(project)

            pipeline.run_voice(
                project,
                api_key=key,
                model=settings.get("tts_model", "") or tts_mod.DEFAULT_MODEL,
                on_progress=voiced,
                cancel=stop,
            )
        elif step == "thumbnail":
            pipeline.run_thumbnail(project, count=int(options.get("frame_count") or 16), cancel=stop)
        else:
            raise ValueError(f"unknown step: {step}")

        project.mark(step, "done", message="")
    except Cancelled:
        # stopping is a choice, not a failure: no red, nothing to fix
        project.mark(step, "idle", message="stopped")
    except Exception as exc:      # noqa: BLE001 - every failure is shown as text
        if stop.is_set():
            project.mark(step, "idle", message="stopped")
        else:
            project.mark(step, "error", error=str(exc) or exc.__class__.__name__)
    finally:
        if release:
            _release(pid)
        push(project)


def start(pid: str, step: str, options: dict) -> None:
    if step not in STEPS:
        raise HTTPException(400, f"unknown step '{step}'")
    if not _claim(pid, step):
        raise HTTPException(409, "this project already has a step running")
    threading.Thread(
        target=run_step, args=(pid, step, options), daemon=True
    ).start()


def run_chain(pid: str, steps: list[str], options: dict) -> None:
    """Walk several steps in order, stopping at the first failure."""
    if not _claim(pid, "chain"):
        raise HTTPException(409, "this project already has a step running")

    def worker() -> None:
        try:
            stop = cancel_event(pid)
            for step in steps:
                if stop.is_set():
                    break
                run_step(pid, step, options, release=False)
                project = store.get(pid)
                if not project or project.steps[step].status == "error":
                    break
        finally:
            _release(pid)

    threading.Thread(target=worker, daemon=True).start()


# ------------------------------------------------------------------ models

class CreateRequest(BaseModel):
    url: str = ""
    source_file: str = ""      # a path on this machine, instead of a link
    mode: str = "reels"
    language: str = "en"
    target_seconds: float = 0.0
    autorun: bool = True
    cookies_browser: str = ""
    quality: str = "1080"


class StepRequest(BaseModel):
    api_key: str = ""
    temperature: float = 0.7
    cookies_browser: str = ""
    quality: str = "1080"
    use_scrape: bool | None = None
    use_vision: bool | None = None
    frame_count: int = 16


class EditRequest(BaseModel):
    """Any subset: only what is sent is changed."""

    mode: str | None = None
    framing: str | None = None
    shape: str | None = None
    burn_captions: bool | None = None
    fit_to_voice: bool | None = None
    caption_style: str | None = None
    caption_lang: str | None = None
    language: str | None = None
    target_seconds: float | None = None
    cut_seconds: float | None = None
    beats: list[dict] | None = None
    titles: dict | None = None
    description: dict | None = None
    hashtags: list[str] | None = None
    thumbnail_text: dict | None = None
    transcript: list[dict] | None = None
    voice_lang: str | None = None
    voice_langs: list | None = None
    voice_engine: str | None = None
    local_model: str | None = None
    voice_reference_text: str | None = None
    voice_name: str | None = None
    voice_style: str | None = None
    original_volume: float | None = None
    narration_volume: float | None = None


class VoiceLineRequest(BaseModel):
    """One user-supplied take, base64 encoded, for a single script line."""

    index: int
    lang: str = "my"
    audio_base64: str
    filename: str = ""


class VoiceReferenceRequest(BaseModel):
    """A clip of someone speaking, for the local engine to clone."""

    audio_base64: str
    filename: str = ""
    text: str = ""          # what is said in the clip, if known


class VoicePreviewRequest(BaseModel):
    voice: str = "Kore"
    style: str = ""
    lang: str = "my"
    text: str = ""


class SettingsRequest(BaseModel):
    gemini_key: str | None = None
    gemini_model: str | None = None
    tts_model: str | None = None
    whisper_model: str | None = None
    use_scrape: bool | None = None
    use_vision: bool | None = None


class ThumbnailRequest(BaseModel):
    png_base64: str


# ------------------------------------------------------------------- routes

@router.get("/env")
def environment() -> dict:
    """What this machine can currently do, so the UI can be honest about it."""
    settings = load_settings()
    return {
        "ffmpeg": have_ffmpeg(),
        "whisper": whisper_available(),
        "playwright": playwright_available(),
        "playwright_hint": playwright_hint(),
        "voxcpm": localtts_mod.available(),
        "voxcpm_hint": localtts_mod.install_hint(),
        "voxcpm_device": localtts_mod.device_note(),
        "has_key": bool(settings.get("gemini_key")),
        "busy": sorted(_running.keys()),
        "gemini_model": settings.get("gemini_model", ""),
        "use_scrape": settings.get("use_scrape", True),
        "use_vision": settings.get("use_vision", True),
        "root": str(ROOT),
    }


@router.get("/settings")
def get_settings() -> dict:
    data = load_settings()
    key = data.get("gemini_key", "")
    # never send the key back in full; the UI only needs to know it is set
    return {
        **data,
        "gemini_key": ("*" * 8 + key[-4:]) if key else "",
        "has_key": bool(key),
    }


@router.post("/settings")
def put_settings(req: SettingsRequest) -> dict:
    save_settings(req.model_dump(exclude_none=True))
    return get_settings()


def _auto_model(key: str) -> str:
    """
    Choose a model the key can use.

    Called when settings name none: the hardcoded default is a guess about
    Google's line-up on the day this was written, and keys issued later cannot
    see the older models at all.
    """
    from ..recap.gemini import DEFAULT_MODELS
    try:
        available = set(Gemini.list_models(key))
    except GeminiError:
        return ""
    for name in DEFAULT_MODELS:
        if name in available:
            return name
    for name in sorted(available):
        if "flash" in name and "image" not in name and "tts" not in name:
            return name
    return ""


@router.get("/models")
def models() -> dict:
    """Models this key can use, with the output ceiling each one allows."""
    settings = load_settings()
    key = settings.get("gemini_key", "")
    if not key:
        raise HTTPException(400, "set a Gemini API key first")
    try:
        rows = Gemini.model_details(key)
    except GeminiError as exc:
        raise HTTPException(400, str(exc)) from exc

    chosen = settings.get("gemini_model", "") or _auto_model(key)
    current = next((r for r in rows if r["name"] == chosen), None)
    return {
        "models": [r["name"] for r in rows],
        "details": rows,
        "current": chosen,
        "current_output_limit": (current or {}).get("output_limit", 0),
        "requested_output_tokens": MAX_OUTPUT_TOKENS,
    }


@router.get("/projects")
def projects() -> list[dict]:
    return store.all()


@router.post("/projects")
def create(req: CreateRequest) -> dict:
    url = req.url.strip()
    source_file = req.source_file.strip()
    if not url and not source_file:
        raise HTTPException(400, "give a video URL or pick a file")
    if source_file and not Path(source_file).exists():
        raise HTTPException(400, f"no such file: {source_file}")

    project = store.create(url or Path(source_file).stem)
    if source_file:
        project.source_file = source_file
        project.title = Path(source_file).stem
        project.url = ""
    project.mode = req.mode if req.mode in ("reels", "long") else "reels"
    project.language = req.language if req.language in ("en", "my") else "en"
    project.target_seconds = max(0.0, float(req.target_seconds or 0))
    project.save()

    if req.autorun:
        run_chain(
            project.id,
            ["source", "transcript", "script", "video", "thumbnail"],
            {"cookies_browser": req.cookies_browser, "quality": req.quality},
        )
    return project.snapshot()


@router.get("/projects/{pid}")
def one(pid: str) -> dict:
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    return project.snapshot()


@router.delete("/projects/{pid}")
def remove(pid: str) -> dict:
    if not store.delete(pid):
        raise HTTPException(404, "no such project")
    return {"ok": True}


@router.post("/projects/{pid}/steps/{step}")
def step(pid: str, step: str, req: StepRequest) -> dict:
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    start(pid, step, req.model_dump(exclude_none=True))
    return project.snapshot()


@router.post("/projects/{pid}/run")
def run_all(pid: str, req: StepRequest) -> dict:
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    run_chain(pid, list(STEPS), req.model_dump(exclude_none=True))
    return project.snapshot()


@router.post("/projects/{pid}/cancel")
def cancel(pid: str) -> dict:
    """Stop whatever this project is doing. Safe to call when idle."""
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    cancel_event(pid).set()
    with _running_lock:
        step = _running.get(pid)
    return {"ok": True, "was_running": step or ""}


@router.patch("/projects/{pid}")
def edit(pid: str, req: EditRequest) -> dict:
    """
    Hand edits win.

    Changing the beats invalidates the cut that was made from the old ones,
    so the subtitle files are rewritten and the video is marked stale rather
    than left silently disagreeing with the script on screen.
    """
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")

    patch = req.model_dump(exclude_none=True)
    beats_changed = "beats" in patch
    mode_changed = "mode" in patch and patch["mode"] != project.mode

    for field, value in patch.items():
        setattr(project, field, value)

    if beats_changed or mode_changed:
        project.timeline = []
        project.mark("video", "idle", message="script changed - rebuild the cut")
        project.mark("voice", "idle", message="rebuild the cut, then the voice")

    # a new voice or a new mix means the rendered file no longer matches
    if {"framing", "shape", "burn_captions", "caption_style", "caption_lang"} & set(patch):
        project.mark("video", "idle", message="look changed - rebuild the cut")

    if {"voice_lang", "voice_name", "voice_style"} & set(patch):
        project.mark("voice", "idle", message="voice changed - regenerate")

    pipeline.write_text_assets(project)
    pipeline.write_subtitles(project)
    project.save()
    push(project)
    return project.snapshot()


@router.post("/projects/{pid}/thumbnail")
def save_thumbnail(pid: str, req: ThumbnailRequest) -> dict:
    """
    Store the thumbnail composed in the browser.

    The overlay is drawn on a canvas in the page because Chromium shapes
    Burmese text correctly and the local Pillow build cannot.
    """
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    raw = req.png_base64.split(",", 1)[-1]
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as exc:      # noqa: BLE001
        raise HTTPException(400, f"that is not valid base64 PNG data: {exc}") from exc
    if not blob.startswith(b"\x89PNG"):
        raise HTTPException(400, "the uploaded data is not a PNG")
    project.thumbnail_path.write_bytes(blob)
    project.mark("thumbnail", "done", message="saved")
    push(project)
    return {"ok": True, "path": str(project.thumbnail_path)}


@router.get("/voices")
def voices() -> dict:
    """The voice catalogue, with Google's own character words."""
    return {
        "voices": [
            {"name": v.name, "character": v.character, "note": v.note}
            for v in tts_mod.VOICES
        ],
        "samples": tts_mod.SAMPLES,
        "caption_styles": sorted(media_mod.CAPTION_STYLES),
        "shapes": sorted(media_mod.SHAPES),
        "model": load_settings().get("tts_model", "") or tts_mod.DEFAULT_MODEL,
        "engines": [
            {
                "id": "gemini",
                "name": "Gemini (cloud)",
                "ready": True,
                "note": "30 voices, best Burmese, but the free tier speaks "
                        "about three lines a minute",
            },
            {
                "id": "voxcpm",
                "name": "VoxCPM (local)",
                "ready": localtts_mod.available(),
                "note": "no quota and clones a voice from a sample; "
                        + localtts_mod.device_note(),
                "models": [
                    {"id": k, "note": v}
                    for k, v in localtts_mod.MODELS.items()
                ],
                "default_model": localtts_mod.DEFAULT_MODEL,
                "hint": localtts_mod.install_hint(),
            },
        ],
    }


@router.post("/voice/preview")
def voice_preview(req: VoicePreviewRequest) -> Response:
    """A few seconds of a voice, so it can be judged before committing."""
    key = load_settings().get("gemini_key", "")
    model = load_settings().get("tts_model", "") or tts_mod.DEFAULT_MODEL
    try:
        audio = tts_mod.preview(key, req.voice, req.style, req.lang, model, req.text)
    except GeminiError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(content=audio, media_type="audio/wav")


@router.post("/projects/{pid}/voice/suggest")
def voice_suggest(pid: str) -> dict:
    """Ask which voice suits this particular video."""
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    settings = load_settings()
    key = settings.get("gemini_key", "")
    if not key:
        raise HTTPException(400, "set a Gemini API key first")
    model = settings.get("gemini_model", "") or _auto_model(key)
    try:
        advice = tts_mod.suggest(
            Gemini(key, model),
            project.title,
            (project.description or {}).get("en", ""),
            project.beats,
        )
    except GeminiError as exc:
        raise HTTPException(400, str(exc)) from exc

    project.voice_name = advice["voice_my"] if project.voice_lang == "my" else advice["voice_en"]
    project.voice_style = advice["style"]
    project.voice_reason = advice["reason"]
    project.save()
    push(project)
    return {**advice, "applied": project.voice_name}


@router.post("/projects/{pid}/voice/line")
def upload_voice_line(pid: str, req: VoiceLineRequest) -> dict:
    """
    Take the user's own recording for one line of the script.

    Regenerating the narration leaves these alone, so a line can be fixed by
    hand -- a better read, a name pronounced properly -- without the next
    regenerate overwriting it.
    """
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    if req.index < 0:
        raise HTTPException(400, "line index must not be negative")

    raw = req.audio_base64.split(",", 1)[-1]
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as exc:      # noqa: BLE001
        raise HTTPException(400, f"that is not valid base64 audio: {exc}") from exc
    if len(blob) < 200:
        raise HTTPException(400, "that file is too small to be audio")

    project.voice_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(req.filename or "upload.wav").suffix or ".wav"
    staged = project.voice_dir / f"upload_{req.index:03d}{suffix}"
    staged.write_bytes(blob)

    lang = req.lang if req.lang in ("en", "my") else project.voice_lang
    target = tts_mod.custom_path(project.voice_dir, req.index, lang)
    try:
        to_wav(staged, target)
    except MediaError as exc:
        raise HTTPException(400, f"could not read that audio: {exc}") from exc
    finally:
        staged.unlink(missing_ok=True)

    project.mark("voice", "idle", message="custom line added - regenerate to use it")
    push(project)
    return {"ok": True, "file": target.name}


@router.delete("/projects/{pid}/voice/line")
def delete_voice_line(pid: str, index: int, lang: str = "my") -> dict:
    """Drop a user-supplied take and go back to the generated voice."""
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    target = tts_mod.custom_path(project.voice_dir, index, lang)
    existed = target.exists()
    target.unlink(missing_ok=True)
    push(project)
    return {"ok": True, "removed": existed}


@router.post("/projects/{pid}/voice/reference")
def upload_voice_reference(pid: str, req: VoiceReferenceRequest) -> dict:
    """
    Take a clip of a voice for the local engine to clone.

    VoxCPM2 needs only a few seconds to copy a voice, and it clones across
    languages -- so a Burmese sample of your own voice narrates every line in
    it, which no cloud voice can do. Telling it what the clip says (`text`)
    sharpens the copy; leaving it blank still works.
    """
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")

    raw = req.audio_base64.split(",", 1)[-1]
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as exc:      # noqa: BLE001
        raise HTTPException(400, f"that is not valid base64 audio: {exc}") from exc
    if len(blob) < 2000:
        raise HTTPException(400, "that clip is too short to copy a voice from")

    project.voice_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(req.filename or "reference.wav").suffix or ".wav"
    staged = project.voice_dir / f"reference_upload{suffix}"
    staged.write_bytes(blob)

    target = project.voice_dir / "reference.wav"
    try:
        to_wav(staged, target)
    except MediaError as exc:
        raise HTTPException(400, f"could not read that audio: {exc}") from exc
    finally:
        staged.unlink(missing_ok=True)

    seconds = 0.0
    try:
        from ..recap.media import probe as _probe
        seconds = _probe(target).duration
    except Exception:      # noqa: BLE001 - the length is only advice
        pass

    project.voice_reference = target.name
    project.voice_reference_text = req.text.strip()
    project.mark("voice", "idle", message="voice sample added - regenerate to use it")
    project.save()
    push(project)
    return {
        "ok": True,
        "file": target.name,
        "seconds": round(seconds, 1),
        "hint": ("shorter than 3 seconds is thin for cloning"
                 if 0 < seconds < 3 else ""),
    }


SAMPLE_LINE = {
    "en": "Here is how this voice sounds reading a line of your recap.",
    "my": "ပြန်လည်တင်ပြသော "
          "စာသားကို "
          "ဒီအသံဖြင့် ဖတ်ပြသည်။",
}


@router.post("/projects/{pid}/voice/candidates")
def voice_candidates(pid: str, count: int = 4, lang: str = "") -> dict:
    """
    Audition several local voices and keep the one that sounds right.

    VoxCPM has no named voices and no seed: with nothing to copy it invents a
    fresh speaker on every call. That is why the voice list is empty for the
    local engine -- there is no catalogue to list. So build one here: speak the
    same sample line a few times, keep each take, and whichever one is chosen
    becomes the reference clip the whole narration is cloned from. It is the
    same picker the cloud voices get, with the voices made on the spot.
    """
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")

    from ..recap import localtts
    if not localtts.available():
        raise HTTPException(400, "the local voice engine is not installed")

    lang = lang if lang in ("en", "my") else (project.voice_lang or "en")
    count = max(1, min(6, int(count or 4)))
    text = SAMPLE_LINE[lang]

    out = project.voice_dir / "candidates"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("cand_*.wav"):
        old.unlink(missing_ok=True)
    for old in out.glob("cand_*.txt"):
        old.unlink(missing_ok=True)

    rows = []
    for i in range(count):
        try:
            # no reference and no anchor: each call is a different speaker,
            # which is the whole point here
            audio = localtts.speak(
                text,
                model_id=project.local_model or localtts.DEFAULT_MODEL,
                cancel=cancel_event(pid),
            )
        except Exception as exc:      # noqa: BLE001
            if not rows:
                raise HTTPException(400, f"could not generate a voice: {exc}") from exc
            break                     # keep whatever was managed
        wav = out / f"cand_{i}.wav"
        wav.write_bytes(audio)
        (out / f"cand_{i}.txt").write_text(text, encoding="utf-8")
        rows.append({
            "index": i,
            "file": f"voice/candidates/{wav.name}",
            "lang": lang,
        })

    return {"ok": True, "lang": lang, "text": text, "candidates": rows}


@router.post("/projects/{pid}/voice/candidates/select")
def pick_voice_candidate(pid: str, index: int) -> dict:
    """Adopt an auditioned voice as the one the narration is spoken in."""
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")

    src = project.voice_dir / "candidates" / f"cand_{int(index)}.wav"
    if not src.exists():
        raise HTTPException(404, "that voice is no longer available - audition again")
    said = src.with_suffix(".txt")

    target = project.voice_dir / "reference.wav"
    target.write_bytes(src.read_bytes())
    project.voice_reference = target.name
    # the sample text is known exactly, which is the sharpest kind of clone
    project.voice_reference_text = said.read_text(encoding="utf-8") if said.exists() else ""
    project.mark("voice", "idle", message="voice chosen - regenerate to use it")
    project.save()
    push(project)
    return {"ok": True, "file": target.name}


@router.delete("/projects/{pid}/voice/reference")
def clear_voice_reference(pid: str) -> dict:
    """Go back to the model's own voice."""
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    (project.voice_dir / "reference.wav").unlink(missing_ok=True)
    project.voice_reference = ""
    project.voice_reference_text = ""
    project.mark("voice", "idle", message="voice sample removed - regenerate")
    project.save()
    push(project)
    return {"ok": True}


@router.get("/projects/{pid}/voice/lines")
def voice_lines(pid: str, lang: str = "") -> dict:
    """Which lines have audio, and whether it was generated or supplied."""
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    lang = lang or project.voice_lang
    rows = []
    for i, row in enumerate(
        [r for r in project.timeline if (r.get(lang) or "").strip()]
    ):
        mine = tts_mod.custom_path(project.voice_dir, i, lang)
        generated = project.voice_dir / f"line_{i:03d}_{lang}.wav"
        has_custom = mine.exists()
        rows.append({
            "index": i,
            "text": row.get(lang, ""),
            "at": row.get("recap_start", 0),
            "custom": has_custom,
            "file": mine.name if has_custom else (generated.name if generated.exists() else ""),
        })
    return {"lang": lang, "lines": rows}


@router.get("/projects/{pid}/transcript.srt")
def transcript_srt(pid: str, lang: str = "") -> PlainTextResponse:
    """
    The original transcript as SRT, optionally translated.

    Without `lang` this is the transcript exactly as captured. With one, the
    lines are translated but keep their original timings, so the file still
    matches the source video frame for frame.
    """
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    if not project.transcript:
        raise HTTPException(400, "there is no transcript yet")

    cues = [Cue(**c) for c in project.transcript]
    name = "transcript.srt"

    if lang and lang != (project.transcript_language or "").split("-")[0]:
        settings = load_settings()
        key = settings.get("gemini_key", "")
        if not key:
            raise HTTPException(400, "a Gemini API key is needed to translate")
        model = settings.get("gemini_model", "") or _auto_model(key)

        cached = project.dir / f"transcript_{lang}.srt"
        if cached.exists():
            return PlainTextResponse(
                cached.read_text(encoding="utf-8"),
                headers={"Content-Disposition": f'attachment; filename="transcript_{lang}.srt"'},
                media_type="application/x-subrip",
            )
        # Hand over the recap script in the same language as a glossary: the
        # user reads both, and a name spelled two different ways across the two
        # files reads as sloppiness rather than as two translations.
        known = [
            (b.get(lang) or "").strip()
            for b in (project.beats or [])
            if (b.get(lang) or "").strip()
        ][:12]
        glossary = ""
        if known:
            glossary = (
                "The recap script for this same video is already written in "
                + ("Burmese" if lang == "my" else "English")
                + ". Match its spelling of names and its wording for recurring "
                "terms:\n" + "\n".join("- " + k for k in known)
            )

        try:
            texts = script_mod.translate_cues(
                Gemini(key, model), cues, lang,
                title=project.title, glossary=glossary,
            )
        except GeminiError as exc:
            raise HTTPException(400, str(exc)) from exc
        for cue, text in zip(cues, texts):
            cue.text = text
        name = f"transcript_{lang}.srt"
        cached.write_text(to_srt(cues), encoding="utf-8")

    return PlainTextResponse(
        to_srt(cues),
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
        media_type="application/x-subrip",
    )


class CleanupRequest(BaseModel):
    """Which categories of working file to remove."""

    working: bool = True      # frames, vision shots, page screenshot
    narration: bool = False   # the spoken clips, once they are in the video
    cuts: bool = False        # the recap cuts, rebuildable from source + beats
    source: bool = False      # the original download -- nothing rebuilds after


def _cleanup_plan(project) -> dict:
    """
    What each category holds, and what losing it costs.

    Kept out of the delete path on purpose: the same function answers "what
    would this free" for the preview and "what should go" for the deletion, so
    the number shown is the number acted on.
    """
    def files(*globs):
        found = []
        for pattern in globs:
            found += [p for p in project.dir.glob(pattern) if p.is_file()]
        return found

    finals = set(project.finals().values())

    cuts = [
        p for p in files("recap_*.mp4")
        if p.name not in finals
    ]

    return {
        "working": {
            "files": files("frames/*", "vision/*", "page.png", "*.tmp"),
            "label": "Thumbnail candidates, the frames sent to the model, page screenshot",
            "cost": "Re-run Frames to get thumbnail candidates back.",
        },
        "narration": {
            "files": [
                p for p in files("voice/*")
                if not p.name.endswith("_custom.wav") and p.name != "reference.wav"
            ],
            "label": "The spoken clips, already mixed into the final video",
            "cost": "Regenerating the voice re-speaks every line.",
        },
        "cuts": {
            "files": cuts,
            "label": "Recap cuts without narration",
            "cost": "Rebuild the cut to get them back -- needs the source video.",
        },
        "source": {
            "files": files("source.*"),
            "label": "The original video, as downloaded",
            "cost": "NOTHING can be rebuilt afterwards. The finished video, "
                    "script, subtitles and thumbnail all stay.",
        },
    }


@router.get("/projects/{pid}/cleanup")
def cleanup_preview(pid: str) -> dict:
    """How much each category would free, without deleting anything."""
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")

    plan = _cleanup_plan(project)
    groups = []
    for key, entry in plan.items():
        size = sum(p.stat().st_size for p in entry["files"] if p.exists())
        groups.append({
            "key": key,
            "label": entry["label"],
            "cost": entry["cost"],
            "files": len(entry["files"]),
            "bytes": size,
        })

    total = sum(
        p.stat().st_size
        for p in project.dir.rglob("*")
        if p.is_file()
    )
    kept = sum(
        p.stat().st_size
        for p in project.dir.glob("final_*.mp4")
        if p.is_file()
    )
    return {"groups": groups, "total_bytes": total, "final_bytes": kept}


@router.post("/projects/{pid}/cleanup")
def cleanup(pid: str, req: CleanupRequest) -> dict:
    """
    Delete the working files, keeping everything that cannot be remade.

    The finished videos, the script, both subtitle sets, the thumbnail and the
    text are never touched, whatever is asked for -- those are the reason the
    project exists.
    """
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")

    wanted = {
        "working": req.working,
        "narration": req.narration,
        "cuts": req.cuts,
        "source": req.source,
    }
    plan = _cleanup_plan(project)

    freed = 0
    removed = 0
    for key, entry in plan.items():
        if not wanted.get(key):
            continue
        for path in entry["files"]:
            try:
                size = path.stat().st_size
                path.unlink()
                freed += size
                removed += 1
            except OSError:
                continue

    # empty folders left behind read as clutter
    for folder in ("frames", "vision", "voice"):
        target = project.dir / folder
        if target.is_dir() and not any(target.iterdir()):
            try:
                target.rmdir()
            except OSError:
                pass

    if wanted.get("working"):
        project.thumbnail_candidates = []
    if wanted.get("narration"):
        project.narration = []
    project.save()
    push(project)
    return {"ok": True, "freed_bytes": freed, "files_removed": removed}


@router.get("/projects/{pid}/files")
def project_files(pid: str) -> dict:
    """
    Everything the project has produced, with a plain-English label.

    Each step leaves its raw output on disk -- the untouched download, the cut
    before captions, the narration as separate clips, the frames before any
    text went over them. The UI only ever surfaced a handful of those, so the
    rest may as well not have existed. This lists all of it.
    """
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")

    mode = project.mode
    langs = {"en": "English", "my": "Burmese"}

    def label(rel: str) -> tuple[str, str]:
        """(group, description) for a file, or ('', '') to leave it out."""
        name = rel.split("/")[-1]
        if name == "source.mp4":
            return "Source", "The original video, exactly as downloaded"
        if name == f"recap_{mode}.mp4":
            return "Video", "Recap cut -- no captions, no narration"
        if name == f"recap_{mode}_captioned.mp4":
            return "Video", "Recap cut with captions burned in"
        if name.startswith("recap_") and name.endswith(".mp4"):
            return "Video", "Recap cut (%s)" % name.split("_")[1].split(".")[0]
        if name.startswith("final_"):
            code = name.rsplit("_", 1)[-1].replace(".mp4", "")
            return "Video", "Final video -- %s narration" % langs.get(code, code)
        if name == "transcript.srt":
            return "Transcript", "Original transcript, as captured"
        if name == "transcript.txt":
            return "Transcript", "Original transcript, plain text"
        if name.startswith("transcript_") and name.endswith(".srt"):
            code = name.replace("transcript_", "").replace(".srt", "")
            return "Transcript", "Transcript translated to %s" % langs.get(code, code)
        if name.startswith("recap_script_") and name.endswith("_original_timing.srt"):
            code = name.split("_")[2]
            return "Script", "Recap script (%s) timed to the ORIGINAL video" % langs.get(code, code)
        if name.startswith("recap_script_") and name.endswith(".srt"):
            code = name.split("_")[2].replace(".srt", "")
            return "Script", "Recap script (%s) timed to the recap" % langs.get(code, code)
        if name.startswith("recap_script_") and name.endswith(".txt"):
            code = name.split("_")[2].replace(".txt", "")
            return "Script", "Recap script (%s), plain text" % langs.get(code, code)
        if name.startswith("post_"):
            code = name.replace("post_", "").replace(".txt", "")
            return "Copy", "Title, description and hashtags (%s)" % langs.get(code, code)
        if name == "hashtags.txt":
            return "Copy", "Hashtags on their own"
        if name == "thumbnail.png":
            return "Thumbnail", "Thumbnail with your text on it"
        if rel.startswith("frames/"):
            return "Thumbnail", "Candidate frame, no text over it"
        if rel.startswith("voice/") and name.endswith("_custom.wav"):
            return "Narration", "Your own recording for one line"
        if name == "voice/reference.wav" or name == "reference.wav":
            return "Narration", "The voice sample being cloned"
        if rel.startswith("voice/") and name.endswith(".wav"):
            return "Narration", "Spoken line, on its own"
        if name == "page.png":
            return "Source", "Screenshot of the page it came from"
        if name == "page_context.json":
            return "Source", "What was read off the page"
        if name == "project.json":
            return "Source", "Everything about this project, as data"
        return "", ""

    groups: dict[str, list] = {}
    for path in sorted(project.dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(project.dir).as_posix()
        if rel.endswith(".tmp") or "/_parts/" in rel or rel.startswith("vision/"):
            continue
        group, description = label(rel)
        if not group:
            continue
        groups.setdefault(group, []).append({
            "name": rel,
            "label": description,
            "bytes": path.stat().st_size,
        })

    order = ["Video", "Narration", "Script", "Transcript", "Copy", "Thumbnail", "Source"]
    return {
        "groups": [
            {"name": g, "files": groups[g]} for g in order if g in groups
        ],
        "folder": str(project.dir),
    }


@router.get("/projects/{pid}/srt")
def srt(pid: str, lang: str = "en", timing: str = "recap") -> PlainTextResponse:
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")

    rows = project.timeline
    if not rows:
        # Editing the script clears the timeline, because the old cut no longer
        # matches the new beats. Subtitles against the ORIGINAL still hold --
        # the beats carry their own source timings and never needed the cut --
        # so fall back to those rather than refusing a file we can produce.
        if timing != "original":
            raise HTTPException(
                400,
                "the cut does not match the edited script yet. Rebuild the cut "
                "for recap-timed subtitles, or download the original timing, "
                "which works straight from the script.",
            )
        rows = [{
            "source_start": float(b.get("start") or 0),
            "source_end": float(b.get("end") or 0),
            "recap_start": float(b.get("start") or 0),
            "recap_end": float(b.get("end") or 0),
            "en": b.get("en", ""),
            "my": b.get("my", ""),
        } for b in project.beats]
        if not rows:
            raise HTTPException(400, "write the recap script first")

    body = (
        source_srt(rows, lang) if timing == "original"
        else recap_srt(rows, lang)
    )
    name = f"recap_{lang}_{timing}.srt"
    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
        media_type="application/x-subrip",
    )


@router.get("/projects/{pid}/file")
def file(pid: str, name: str):
    """Serve one file from inside the project folder, and nothing outside it."""
    project = store.get(pid)
    if not project:
        raise HTTPException(404, "no such project")
    target = (project.dir / name).resolve()
    try:
        target.relative_to(project.dir.resolve())
    except ValueError:
        raise HTTPException(403, "that path is outside the project") from None
    if not target.is_file():
        raise HTTPException(404, f"{name} does not exist yet")
    return FileResponse(target)


@router.get("/events")
async def events() -> StreamingResponse:
    import asyncio

    q: queue.Queue = queue.Queue(maxsize=256)
    with _listeners_lock:
        _listeners.append(q)

    async def stream():
        loop = asyncio.get_running_loop()
        try:
            for snap in store.all():
                yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
            while True:
                try:
                    item = await loop.run_in_executor(None, q.get, True, 20)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            with _listeners_lock:
                if q in _listeners:
                    _listeners.remove(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
