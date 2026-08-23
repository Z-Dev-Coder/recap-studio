"""
The five steps, each runnable on its own.

Nothing here calls the next step automatically. "Run all" is the web layer
walking this list, so regenerating just the script -- or just the video after
hand-editing the beats -- costs exactly one step and leaves everything else
untouched.
"""

from __future__ import annotations

from pathlib import Path

from . import scrape as scrape_mod
from . import script as script_mod
from . import thumbnail as thumb_mod
from . import tts as tts_mod
from . import video as video_mod
from .media import (
    Cancelled,
    MediaError,
    burn_subtitles,
    frame_at,
    have_ffmpeg,
    mux_narration,
    probe,
)
from .project import Project
from .transcript import (
    Cue,
    TranscriptError,
    from_platform,
    from_whisper,
    plain_text,
    to_srt,
    whisper_available,
)


class StepError(RuntimeError):
    """A step failed for a reason worth showing the user verbatim."""


# ------------------------------------------------------------------ 1. source

def run_source(project: Project, cookies_browser: str = "", quality: str = "1080", cancel=None) -> None:
    """
    Fetch the original video and its metadata.

    yt-dlp has no stop method, so a cancel is delivered by raising out of the
    progress hook it calls on every chunk.
    """
    import yt_dlp

    from ..downloader import _js_runtimes

    dest = project.source_path
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "js_runtimes": _js_runtimes(),
        "outtmpl": str(dest.with_suffix("")) + ".%(ext)s",
        "format": (
            f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/"
            "bestvideo+bestaudio/best"
        ),
        "merge_output_format": "mp4",
        "windowsfilenames": True,
        "retries": 5,
    }
    if cancel is not None:
        def _watch(_status: dict) -> None:
            if cancel.is_set():
                raise Cancelled()
        options["progress_hooks"] = [_watch]
    if cookies_browser:
        options["cookiesfrombrowser"] = (cookies_browser,)

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(project.url, download=True)
    except Cancelled:
        raise
    except Exception as exc:      # noqa: BLE001 - yt-dlp errors are user-facing
        # yt-dlp wraps whatever the hook raised, so a stop can arrive disguised
        if cancel is not None and cancel.is_set():
            raise Cancelled() from exc
        raise StepError(str(exc)) from exc

    if not dest.exists():
        # the merge may have produced .mkv or .webm depending on the source
        for candidate in sorted(project.dir.glob("source.*")):
            if candidate.suffix.lower() in (".mp4", ".mkv", ".webm"):
                candidate.rename(dest)
                break
    if not dest.exists():
        raise StepError("the download finished but no video file appeared")

    project.title = info.get("title") or project.title
    project.uploader = info.get("uploader") or info.get("channel") or ""
    # yt-dlp reads these from the site's own API, so unlike the page's meta
    # tags they always belong to THIS video
    project.source_description = (info.get("description") or "")[:4000]
    project.source_tags = [t for t in (info.get("tags") or []) if t][:30]
    project.duration = float(info.get("duration") or 0)
    if not project.duration and have_ffmpeg():
        project.duration = probe(dest).duration
    project.save()


# -------------------------------------------------------------- 2. transcript

def run_transcript(project: Project, cookies_browser: str = "", whisper_model: str = "small", cancel=None) -> None:
    """Platform captions if they exist, local transcription if they do not."""
    cues: list[Cue] = []
    language = ""

    if project.url:
        try:
            cues, language = from_platform(project.url, cookies_browser)
        except Exception:      # noqa: BLE001 - fall through to transcription
            cues, language = [], ""

    if not cues:
        if not project.source_path.exists():
            raise StepError(
                "This video has no captions, so it must be transcribed from the "
                "audio -- download the source first."
            )
        if not whisper_available():
            raise TranscriptError(
                "This video has no captions on the platform, so it needs local "
                "transcription.\nInstall it once with:\n"
                r"  venv\Scripts\python.exe -m pip install faster-whisper"
            )
        cues, language = from_whisper(project.source_path, whisper_model, cancel=cancel)

    if not cues:
        raise StepError("no speech could be found in this video")

    project.transcript = [c.as_dict() for c in cues]
    project.transcript_language = language
    (project.dir / "transcript.srt").write_text(to_srt(cues), encoding="utf-8")
    (project.dir / "transcript.txt").write_text(plain_text(cues), encoding="utf-8")
    project.save()


# ------------------------------------------------------------------ 3. script

def collect_frames(project: Project, duration: float, count: int) -> list[tuple[str, bytes]]:
    """
    One frame from the middle of each chapter, for the model to look at.

    Screen recordings, tutorials and slide decks carry most of their meaning
    in the picture, and a transcript cannot see a diagram. Capped in number
    because each image costs quota.
    """
    if not project.source_path.exists() or duration <= 0:
        return []
    shots_dir = project.dir / "vision"
    shots_dir.mkdir(parents=True, exist_ok=True)
    frames: list[tuple[str, bytes]] = []
    count = max(1, min(count, 10))
    step = duration / count
    for i in range(count):
        when = step * (i + 0.5)
        dest = shots_dir / f"chapter_{i:02d}.jpg"
        try:
            frame_at(project.source_path, when, dest, width=768)
            frames.append(("image/jpeg", dest.read_bytes()))
        except Exception:      # noqa: BLE001 - vision is a bonus, never required
            continue
    return frames


def run_scrape(project: Project) -> dict:
    """Read the source page for context. Never fatal: returns what it got."""
    if not project.url:
        return {}
    data = scrape_mod.scrape(project.url, screenshot=project.dir / "page.png")
    (project.dir / "page_context.json").write_text(
        __import__("json").dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def run_script(
    project: Project,
    api_key: str,
    model: str = "",
    temperature: float = 0.7,
    use_scrape: bool = True,
    use_vision: bool = True,
) -> None:
    """Ask Gemini for the recap script, description, hashtags and title."""
    if not project.transcript:
        raise StepError("there is no transcript to write a recap from")
    duration = project.duration or (
        probe(project.source_path).duration if project.source_path.exists() else 0
    )
    if duration <= 0:
        raise StepError("the video duration is unknown, so the recap cannot be aligned")

    cues = [Cue(**c) for c in project.transcript]

    # The creator's own words about the video, straight from yt-dlp: the raw
    # field, without the chapter lists and localised UI labels that come back
    # attached to the same text when it is scraped off the rendered page.
    parts = []
    if project.source_description:
        parts.append("Creator's own description:\n" + project.source_description)
    if project.source_tags:
        parts.append("Creator's tags: " + ", ".join(project.source_tags))

    if use_scrape:
        scraped = run_scrape(project)
        block = scrape_mod.as_prompt_block(scraped)
        if block:
            parts.append(block)

    context = "\n\n".join(parts)

    count, _ = script_mod.beat_plan(duration, project.mode, project.target_seconds)
    frames = collect_frames(project, duration, count) if use_vision else []

    result = script_mod.generate(
        api_key=api_key,
        model=model,
        title=project.title,
        uploader=project.uploader,
        duration=duration,
        cues=cues,
        mode=project.mode,
        target_seconds=project.target_seconds,
        temperature=temperature,
        context=context,
        frames=frames,
    )

    project.duration = duration
    project.beats = result["beats"]
    project.titles = result["title"]
    project.description = result["description"]
    project.hashtags = result["hashtags"]
    project.thumbnail_text = result["thumbnail_text"]
    project.coverage = result["coverage"]
    project.video_type = result.get("video_type", "")
    project.pacing = result.get("pacing", "")
    project.hook = result.get("hook") or {"en": "", "my": ""}
    # the old cut no longer matches the new beats
    project.timeline = []
    write_text_assets(project)
    project.save()


# ------------------------------------------------------------------- 4. video

def run_video(project: Project, on_progress=None, cancel=None) -> None:
    """Splice the beats out of the source into the recap cut."""
    if not project.source_path.exists():
        raise StepError("the source video is missing")
    if not project.beats:
        raise StepError("there is no recap script to cut to")
    if not have_ffmpeg():
        raise StepError("ffmpeg was not found on PATH")

    try:
        result = video_mod.build(
            project.source_path,
            project.beats,
            project.recap_path,
            mode=project.mode,
            on_progress=on_progress,
            target_seconds=project.cut_seconds or project.duration,
            duration=project.duration,
            cancel=cancel,
            framing=project.framing or "blur",
            shape=project.shape or "",
        )
    except MediaError as exc:
        raise StepError(str(exc)) from exc

    project.timeline = result["timeline"]
    write_subtitles(project)

    # burned-in captions, for the feeds that autoplay muted
    if project.burn_captions:
        srt = project.dir / f"recap_script_{project.caption_lang or project.voice_lang}.srt"
        if srt.exists():
            try:
                burned = project.dir / f"recap_{project.mode}_captioned.mp4"
                burn_subtitles(project.recap_path, srt, burned,
                               style=project.caption_style, cancel=cancel)
                burned.replace(project.recap_path)
            except MediaError as exc:
                raise StepError(f"captions could not be burned in: {exc}") from exc

    project.save()


# --------------------------------------------------------------- 5. thumbnail

def run_thumbnail(project: Project, count: int = 16, cancel=None) -> None:
    """Extract and rank candidate frames for the thumbnail editor."""
    if not project.source_path.exists():
        raise StepError("the source video is missing")
    found = thumb_mod.candidates(
        project.source_path, project.frames_dir, count=count, beats=project.beats,
        cancel=cancel,
    )
    if not found:
        raise StepError("no usable frames could be read from the video")
    project.thumbnail_candidates = [c.as_dict(rel_to=project.dir) for c in found]
    project.save()


# ------------------------------------------------------------------- 6. voice

def run_voice(
    project: Project,
    api_key: str,
    model: str = "",
    on_progress=None,
    cancel=None,
) -> None:
    """
    Speak the recap and lay it over the cut.

    This is the deliverable: the recap footage with narration in the chosen
    language on top and the original audio held underneath at whatever level
    the user set, so the source is still audible without fighting the voice.
    """
    if not project.timeline:
        raise StepError("build the recap cut first -- the voice follows its timings")
    if not have_ffmpeg():
        raise StepError("ffmpeg was not found on PATH")

    lang = project.voice_lang if project.voice_lang in ("en", "my") else "my"
    if not any((row.get(lang) or "").strip() for row in project.timeline):
        raise StepError(f"the recap script has no {lang} lines to speak")

    # A re-run must not blend into the previous take -- but a line the user
    # supplied themselves is theirs, and regenerating the machine voice must
    # not throw it away.
    if project.voice_dir.exists():
        for old in project.voice_dir.glob("line_*.wav"):
            if not old.name.endswith("_custom.wav"):
                old.unlink(missing_ok=True)

    reference = None
    if project.voice_reference:
        candidate = project.voice_dir / project.voice_reference
        if candidate.exists():
            reference = candidate

    made = tts_mod.narrate(
        api_key=api_key,
        timeline=project.timeline,
        out_dir=project.voice_dir,
        lang=lang,
        voice=project.voice_name or "Kore",
        style=project.voice_style,
        model=model or tts_mod.DEFAULT_MODEL,
        on_progress=on_progress,
        cancel=cancel,
        engine=project.voice_engine or "gemini",
        reference_audio=reference,
        reference_text=project.voice_reference_text,
    )
    if not made:
        raise StepError("no narration audio was produced")

    clips = [{"path": project.voice_dir / m["file"], "at": m["at"]} for m in made]
    try:
        mux_narration(
            project.recap_path,
            clips,
            project.final_path,
            original_volume=project.original_volume,
            narration_volume=project.narration_volume,
            cancel=cancel,
        )
    except MediaError as exc:
        raise StepError(str(exc)) from exc

    project.narration = made
    project.save()


# ------------------------------------------------------------------- outputs

def write_subtitles(project: Project) -> None:
    """SRT for both languages, timed to the recap and to the original."""
    if not project.timeline:
        return
    for lang in ("en", "my"):
        recap = video_mod.recap_srt(project.timeline, lang)
        if recap.strip():
            (project.dir / f"recap_script_{lang}.srt").write_text(recap, encoding="utf-8")
        source = video_mod.source_srt(project.timeline, lang)
        if source.strip():
            (project.dir / f"recap_script_{lang}_original_timing.srt").write_text(
                source, encoding="utf-8"
            )


def write_text_assets(project: Project) -> None:
    """The copy-paste files: title, description, hashtags, plain script."""
    tags = " ".join("#" + t.lstrip("#") for t in project.hashtags)
    (project.dir / "hashtags.txt").write_text(tags, encoding="utf-8")

    for lang in ("en", "my"):
        title = (project.titles or {}).get(lang, "")
        body = (project.description or {}).get(lang, "")
        if not (title or body):
            continue
        post = f"{title}\n\n{body}\n\n{tags}\n".lstrip()
        (project.dir / f"post_{lang}.txt").write_text(post, encoding="utf-8")

        lines = []
        for b in project.beats:
            text = (b.get("my") if lang == "my" else b.get("en")) or ""
            if text.strip():
                lines.append(f"[{_stamp(b.get('start', 0))}] {text.strip()}")
        if lines:
            (project.dir / f"recap_script_{lang}.txt").write_text(
                "\n\n".join(lines), encoding="utf-8"
            )


def _stamp(seconds: float) -> str:
    seconds = max(0, int(float(seconds or 0)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
