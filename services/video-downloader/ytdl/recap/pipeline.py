"""
The five steps, each runnable on its own.

Nothing here calls the next step automatically. "Run all" is the web layer
walking this list, so regenerating just the script -- or just the video after
hand-editing the beats -- costs exactly one step and leaves everything else
untouched.
"""

from __future__ import annotations

import hashlib

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

# a beat of silence before the narrator starts and after they finish, so the
# picture does not change on the same frame as the last syllable
VOICE_LEAD = 0.4
VOICE_TAIL = 0.8
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

    A project can start from a file on disk instead of a link, in which case
    there is nothing to download -- the file is copied in and probed for the
    duration the rest of the pipeline needs.

    yt-dlp has no stop method, so a cancel is delivered by raising out of the
    progress hook it calls on every chunk.
    """
    if project.source_file:
        return _adopt_local_file(project)

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


def _adopt_local_file(project: Project) -> None:
    """Take a video the user already has and treat it as the source."""
    import shutil

    origin = Path(project.source_file)
    if not origin.exists():
        raise StepError(f"that file is no longer there: {origin}")
    if not have_ffmpeg():
        raise StepError("ffmpeg was not found on PATH")

    dest = project.source_path
    if not dest.exists() or dest.stat().st_size != origin.stat().st_size:
        # copied rather than referenced: the project folder has to stay
        # self-contained, and the original must not be touched
        shutil.copy2(origin, dest)

    info = probe(dest)
    if info.duration <= 0:
        raise StepError("ffmpeg could not read a duration from that file")
    project.duration = info.duration
    if not project.title:
        project.title = origin.stem
    project.save()


# -------------------------------------------------------------- 2. transcript

def run_transcript(project: Project, cookies_browser: str = "", whisper_model: str = "small", cancel=None) -> None:
    """Platform captions if they exist, local transcription if they do not."""
    cues: list[Cue] = []
    language = ""

    if project.url and not project.source_file:
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
    # These now feed the story analysis rather than the narration prompt, and
    # they are the only evidence for anything that happens without being said,
    # so a few more of them is worth the quota on a video that shows more than
    # it tells. Still capped: images are the expensive part of a call.
    count = max(1, min(count, 16))
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
    cancel=None,
    light_model: str = "",
    light_analysis: bool = False,
    quality: str = "",
    stage_models: dict | None = None,
    keys: dict | None = None,
    ollama_url: str = "",
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

    # The length the user actually set lives on the cut slider, so prefer it:
    # planning the script to one length and the cut to another is how the two
    # ended up disagreeing.
    wanted = project.target_seconds or project.cut_seconds or 0.0
    count, _ = script_mod.beat_plan(duration, project.mode, wanted)
    frames = collect_frames(project, duration, count) if use_vision else []

    result = script_mod.generate(
        api_key=api_key,
        model=model,
        title=project.title,
        uploader=project.uploader,
        duration=duration,
        cues=cues,
        mode=project.mode,
        target_seconds=wanted,
        temperature=temperature,
        context=context,
        frames=frames,
        treatment=project.content_type or "recap",
        cancel=cancel,
        light_model=light_model,
        light_analysis=light_analysis,
        quality=quality,
        stage_models=stage_models,
        keys=keys,
        ollama_url=ollama_url,
    )

    project.duration = duration
    project.beats = result["beats"]
    project.titles = result["title"]
    project.description = result["description"]
    project.hashtags = result["hashtags"]
    project.thumbnail_text = result["thumbnail_text"]
    project.coverage = result["coverage"]
    project.story = result.get("story") or {}
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

    # The narration now exists, so the cut is sized to it: each moment gets the
    # footage its line needs, and no more.
    wants = None
    if project.fit_to_voice and project.narration:
        spoken = {}
        for m in project.narration:
            i = int(m.get("index", -1))
            secs = float(m.get("seconds") or 0)
            if secs > spoken.get(i, 0):
                spoken[i] = secs        # the longest language wins the space
        if spoken:
            wants = [
                (spoken.get(int(b.get("index", i)), 0) or 0) + VOICE_LEAD + VOICE_TAIL
                if spoken.get(int(b.get("index", i))) else 0.0
                for i, b in enumerate(project.beats)
            ]
            if not any(w > 0 for w in wants):
                wants = None

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
            fit_seconds=wants,
        )
    except MediaError as exc:
        raise StepError(str(exc)) from exc

    project.timeline = result["timeline"]
    write_subtitles(project)

    # Fitting to the voice can land far short of the length that was asked for,
    # and silently returning a one-minute video to someone who asked for five
    # looks like a bug rather than a consequence. Say which it is.
    asked = project.cut_seconds or project.duration
    got = float(result.get("duration") or 0)
    if wants and asked and got < asked * 0.6:
        project.mark(
            "video", "running",
            message=("{:.0f}s of narration only needs {:.0f}s of footage, so the "
                     "cut is shorter than the {:.0f}s asked for. Regenerate the "
                     "script for fuller lines, or untick 'trim to the narration'."
                     ).format(sum(w for w in wants if w), got, asked),
        )

    # burned-in captions, for the feeds that autoplay muted
    # Captions are burned into their OWN file. Overwriting the clean cut would
    # mean the only way back to an uncaptioned video is to rebuild it, and the
    # captioned one is a preference rather than a correction.
    _reburn_captions(project, cancel=cancel)
    project.save()


def _reburn_captions(project: Project, cancel=None) -> None:
    """
    Redraw the burned-in captions against the current cut.

    Captions are burned into their OWN file. Overwriting the clean cut would
    mean the only way back to an uncaptioned video is to rebuild it, and the
    captioned one is a preference rather than a correction. Re-cutting the
    video invalidates them, because their timings are the recap's timings.
    """
    captioned = project.captioned_path
    captioned.unlink(missing_ok=True)
    if not project.burn_captions:
        return
    srt = project.dir / f"recap_script_{project.caption_lang or project.voice_lang}.srt"
    if not srt.exists():
        return
    try:
        burn_subtitles(project.recap_path, srt, captioned,
                       style=project.caption_style, cancel=cancel)
    except MediaError as exc:
        raise StepError(f"captions could not be burned in: {exc}") from exc


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
    """Narrate every language the project asks for."""
    wanted = project.voice_langs or [project.voice_lang or "my"]
    for index, language in enumerate(wanted):
        _narrate_one(
            project, api_key, model, language,
            on_progress=on_progress, cancel=cancel,
            prefix=(f"{language} " if len(wanted) > 1 else ""),
        )
    # the language shown in the UI follows the last one rendered
    project.voice_lang = wanted[-1]
    project.save()


def _narrate_one(
    project: Project,
    api_key: str,
    model: str = "",
    language: str = "my",
    on_progress=None,
    cancel=None,
    prefix: str = "",
) -> None:
    """
    Speak the recap, one clip per line.

    This runs BEFORE the cut, because the cut is fitted to the narration: the
    old order asked the voice to fit footage whose length had been chosen
    before anybody knew how long the lines would be, which is what left five
    seconds of speech sitting in a thirty-second clip.

    Nothing is laid over the video here -- that is the final step, so the mix
    can be adjusted without paying to speak every line again.
    """
    if not project.beats:
        raise StepError("write the recap script first -- there is nothing to speak")
    if not have_ffmpeg():
        raise StepError("ffmpeg was not found on PATH")

    # The cut does not exist yet, so lay the beats out end to end as a stand-in
    # for it. Only the text and the clip length are read from this; the real
    # positions are worked out in the final step, once the cut is built.
    if not project.timeline:
        playhead = 0.0
        provisional = []
        for i, b in enumerate(project.beats):
            span = max(1.5, float(b.get("end", 0)) - float(b.get("start", 0)))
            provisional.append({
                "index": int(b.get("index", i)),
                "source_start": float(b.get("start", 0)),
                "source_end": float(b.get("end", 0)),
                "recap_start": round(playhead, 3),
                "recap_end": round(playhead + span, 3),
                "score": float(b.get("score", 5) or 5),
                "en": b.get("en", ""), "my": b.get("my", ""),
            })
            playhead += span
        project.timeline = provisional

    lang = language if language in ("en", "my") else "my"
    # final_path is keyed on the language, so each one lands in its own file
    project.voice_lang = lang
    if not any((row.get(lang) or "").strip() for row in project.timeline):
        raise StepError(f"the recap script has no {lang} lines to speak")

    # A re-run must not blend two different voices together -- but it must not
    # throw away good work either. Lines cost a couple of minutes each locally,
    # so a run that fails on line 10 should resume, not start from nothing.
    # Previous clips are cleared only when the settings that shaped them have
    # actually changed. A line the user supplied themselves is always kept.
    # The words matter as much as the voice: a rewritten script must not be
    # narrated with clips of the old wording just because the filenames match.
    spoken_text = "\n".join((row.get(lang) or "") for row in project.timeline)
    signature = "|".join([
        project.voice_engine or "gemini",
        project.local_model or "",
        project.voice_name or "",
        project.voice_style or "",
        lang,
        model or "",
        hashlib.sha1(spoken_text.encode("utf-8")).hexdigest()[:12],
    ])
    stamp = project.voice_dir / "settings.txt"
    previous = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else ""

    if project.voice_dir.exists() and previous != signature:
        for stale in project.voice_dir.glob("line_*.wav"):
            if not stale.name.endswith("_custom.wav"):
                stale.unlink(missing_ok=True)
    project.voice_dir.mkdir(parents=True, exist_ok=True)
    stamp.write_text(signature, encoding="utf-8")

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
        local_model=project.local_model or "",
        reference_audio=reference,
        reference_text=project.voice_reference_text,
    )
    if not made:
        raise StepError("no narration audio was produced")

    # The narration is kept as clips. Laying it over the picture, at whatever
    # mix and speed the user settles on, is the final step -- so the mix can be
    # changed without paying to speak every line again.
    project.narration = (project.narration or []) + made if prefix else made
    project.save()


# --------------------------------------------------------------- final video

def run_final(project: Project, on_progress=None, cancel=None) -> None:
    """
    Lay the narration over the cut and produce the video that gets posted.

    Split out from the voice step so that regenerating a line, changing the
    mix, or re-cutting does not re-render the whole thing every time -- and so
    nothing is rendered at all until the script, the voice and the framing have
    been settled.
    """
    if not project.narration:
        raise StepError("generate the narration first")
    if not project.recap_path.exists():
        raise StepError("build the recap cut first")
    if not have_ffmpeg():
        raise StepError("ffmpeg was not found on PATH")

    langs = project.voice_langs or [project.voice_lang or "my"]
    for lang in langs:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        project.voice_lang = lang

        rows = [m for m in project.narration
                if str(m.get("file", "")).endswith(f"_{lang}.wav")] or project.narration

        # The cut has been rebuilt since the lines were spoken, so a line's
        # position comes from the timeline it will actually play over, not from
        # wherever it sat when it was recorded.
        by_index = {int(r.get("index", -1)): r for r in project.timeline}
        clips = []
        for m in rows:
            row = by_index.get(int(m.get("index", -1)))
            at = float(row["recap_start"]) + VOICE_LEAD if row else float(m.get("at") or 0)
            at = max(0.0, at)
            # Write the position back. It was computed for the mux and thrown
            # away, so the saved narration kept the provisional positions the
            # lines were recorded against -- the file was right and the data
            # describing it was wrong, which is worse than either.
            m["at"] = round(at, 3)
            path = project.voice_dir / m["file"]
            if path.exists():
                clips.append({"path": path, "at": at})
        if not clips:
            continue

        base = project.captioned_path if project.captioned_path.exists() else project.recap_path
        try:
            mux_narration(
                base,
                clips,
                project.final_path,
                original_volume=project.original_volume,
                narration_volume=project.narration_volume,
                speed=project.narration_speed or 1.0,
                cancel=cancel,
            )
        except MediaError as exc:
            raise StepError(str(exc)) from exc
        if on_progress:
            on_progress(langs.index(lang) + 1, len(langs))

    project.voice_lang = langs[-1]
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
