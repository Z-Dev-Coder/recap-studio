"""
ffmpeg/ffprobe wrappers.

Everything that touches the video file goes through here so the rest of the
package can talk in segments and seconds instead of command-line flags.
"""

from __future__ import annotations

import json
import shutil
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# Windows: stop a console window flashing up for every ffmpeg call
_NO_WINDOW = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


class MediaError(RuntimeError):
    """ffmpeg is missing, or refused to do what was asked."""


class Cancelled(Exception):
    """The user stopped the job. Raised out of whatever was running."""


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise MediaError(
            f"{name} was not found on PATH. Install ffmpeg (it ships ffprobe too) "
            "and reopen the app."
        )
    return path


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _run(args: list[str], timeout: int = 3600, cancel=None,
         on_progress=None, seconds: float = 0.0) -> str:
    """
    Run a tool to completion, or until the user stops it.

    With a `cancel` event this polls instead of blocking, so a Stop press
    kills the encoder within a fraction of a second rather than after the
    several minutes a long re-encode would otherwise take.

    `on_progress` is called with a fraction from 0 to 1 as ffmpeg works, which
    needs `seconds` -- the length of the output -- to divide by. Burning
    captions into a two-minute cut takes two minutes, and two minutes of a
    button that looks stuck is indistinguishable from a button that is.
    """
    watching = on_progress is not None and seconds > 0
    if watching:
        # -progress writes machine-readable lines; -nostats silences the
        # human ones, which would otherwise be most of what we parse.
        args = [args[0], "-progress", "pipe:1", "-nostats"] + list(args[1:])
        if cancel is None:
            cancel = threading.Event()      # the polling path is where we read

    if cancel is None:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
        if proc.returncode != 0:
            # ffmpeg puts the useful line at the very end of a wall of banner text
            tail = (proc.stderr or "").strip().splitlines()
            raise MediaError("\n".join(tail[-4:]) or f"{args[0]} failed")
        return proc.stdout

    # Output goes to temp FILES, not pipes. Polling for the cancel flag means
    # nothing is draining the pipes meanwhile, and ffmpeg writes enough progress
    # chatter to stderr to fill the ~64KB buffer and block forever on any
    # encode long enough to be worth cancelling.
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        popen = subprocess.Popen(
            args,
            stdout=out_f,
            stderr=err_f,
            creationflags=_NO_WINDOW,
        )
        deadline = time.monotonic() + timeout
        read_at = 0
        last_told = -1.0
        try:
            while popen.poll() is None:
                if cancel.is_set():
                    popen.kill()
                    popen.wait(timeout=10)
                    raise Cancelled()
                if time.monotonic() > deadline:
                    popen.kill()
                    raise MediaError(f"{args[0]} timed out")
                if watching:
                    out_f.seek(read_at)
                    fresh = out_f.read()
                    read_at += len(fresh)
                    done = _how_far(fresh.decode("utf-8", "replace"), seconds)
                    # only when it has actually moved: a snapshot pushed per
                    # poll is fifty a second for no new information
                    if done is not None and done - last_told >= 0.01:
                        last_told = done
                        on_progress(done)
                time.sleep(0.15)
        finally:
            if popen.poll() is None:
                popen.kill()

        err_f.seek(0)
        err = err_f.read().decode("utf-8", "replace")
        out_f.seek(0)
        out = out_f.read().decode("utf-8", "replace")

    if popen.returncode != 0:
        tail = err.strip().splitlines()
        raise MediaError("\n".join(tail[-4:]) or f"{args[0]} failed")
    return out


def _how_far(text: str, seconds: float) -> float | None:
    """
    How far through the output ffmpeg has got, from its -progress lines.

    It reports out_time_us (or the older out_time_ms, which is also
    microseconds despite the name -- a long-standing wart worth knowing about
    rather than being caught by).
    """
    at = None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() in ("out_time_us", "out_time_ms"):
            try:
                at = int(value.strip()) / 1_000_000
            except ValueError:
                continue
    if at is None:
        return None
    return max(0.0, min(1.0, at / seconds))


@dataclass
class Probe:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool

    @property
    def is_vertical(self) -> bool:
        return self.height >= self.width


def probe(path: Path) -> Probe:
    out = _run([
        _tool("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ], timeout=120)
    data = json.loads(out)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    fps = 30.0
    raw = video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"
    try:
        num, _, den = raw.partition("/")
        if den and float(den) != 0:
            fps = float(num) / float(den)
    except (TypeError, ValueError):
        pass

    duration = 0.0
    for source in (data.get("format", {}), video):
        try:
            duration = float(source.get("duration") or 0) or duration
        except (TypeError, ValueError):
            continue

    return Probe(
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps or 30.0,
        has_audio=audio is not None,
    )


# How far a clip may be retimed to meet its narration. Beyond these the cure
# is worse than the complaint: below 0.5 the picture crawls, above 2.0 it
# scurries, and either reads as a fault rather than an edit.
SLOWEST = 0.5
FASTEST = 2.0


def cut(src: Path, dest: Path, start: float, end: float, vertical: bool = False,
        cancel=None, framing: str = "blur", fit_to: float = 0.0) -> Path:
    """
    Copy one segment out of `src`, re-encoding so the joins are frame-exact.

    Stream copying would be faster but can only cut on keyframes, which slides
    every clip boundary by up to several seconds -- fatal when the whole point
    is that the clip matches the line of script being spoken over it.

    `fit_to` is the length the finished clip must be. When the footage
    available is not that length -- a beat near the start or end of the video
    has nowhere to grow into -- the picture is retimed to fill it rather than
    left to run out from under the voice. Slowing footage to meet a longer
    line is an ordinary edit; leaving the last seconds of a line playing over
    a frozen or repeated picture is not.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.2, end - start)

    # The factor the picture has to be stretched by to last as long as its
    # line. Above 1 the footage is slowed; below 1 it is quickened.
    stretch = 1.0
    if fit_to and fit_to > 0:
        wanted = max(0.2, float(fit_to))
        if abs(wanted - duration) > 0.08:
            stretch = min(FASTEST, max(SLOWEST, wanted / duration))

    shape = SHAPES.get(str(vertical), None) if isinstance(vertical, str) else None
    if shape:
        w, h = shape
        if framing == "crop":
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                  f"crop={w}:{h},setsar=1")
        else:
            vf = (
                f"split=2[bg][fg];"
                f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},boxblur=28:2[bgb];"
                f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
            )
    elif vertical and framing == "crop":
        # Fill the frame with picture by cropping the sides away. Nothing is
        # letterboxed, but anything at the edges of a wide shot is lost, so it
        # is a choice rather than the default.
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1"
        )
    elif vertical:
        # fill a 1080x1920 frame: a blurred cover behind, the real frame on top
        vf = (
            "split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=28:2[bgb];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    else:
        # even dimensions keep libx264 happy on odd-sized sources
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1"

    af = []
    if abs(stretch - 1.0) > 0.01:
        # setpts multiplies each frame's timestamp, so a factor above 1 spreads
        # the same frames over more time. The original audio has to travel with
        # it or it finishes early and the two drift apart; atempo takes the
        # reciprocal, and stays inside its own 0.5-2.0 range because `stretch`
        # already does.
        vf = f"{vf},setpts={stretch:.4f}*PTS"
        af = ["-af", f"atempo={1 / stretch:.4f}"]

    _run([
        _tool("ffmpeg"), "-y",
        # Both are INPUT options: -t after the input would cap the finished
        # clip instead, which silently threw away exactly the extra length
        # retiming had just created. Bounding the source here leaves the
        # output's length to the filters, which is where it is decided.
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src),
        "-filter_complex" if ((vertical or shape) and framing != "crop") else "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30",
        *af,
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-avoid_negative_ts", "make_zero",
        str(dest),
    ], cancel=cancel)
    return dest


def concat(parts: list[Path], dest: Path, cancel=None) -> Path:
    """Join clips that were all encoded by `cut`, so a stream copy is safe."""
    if not parts:
        raise MediaError("nothing to join")
    dest.parent.mkdir(parents=True, exist_ok=True)
    listing = dest.parent / (dest.stem + "_parts.txt")
    listing.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8"
    )
    try:
        _run([
            _tool("ffmpeg"), "-y", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-c", "copy",
            # concat of copied streams is where the negative start timestamp
            # comes from: each part keeps its own, and the join inherits the
            # first one. Left alone it surfaces much later, as the opening of
            # the finished video jumping back on itself while a player sorts
            # the ordering out.
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart", str(dest),
        ], cancel=cancel)
    finally:
        listing.unlink(missing_ok=True)
    return dest


def frame_at(src: Path, when: float, dest: Path, width: int = 1280, cancel=None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run([
        _tool("ffmpeg"), "-y", "-ss", f"{max(0.0, when):.3f}", "-i", str(src),
        "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "2", str(dest),
    ], timeout=180, cancel=cancel)
    return dest


# Caption looks, as libass style overrides. Colours are &HAABBGGRR -- ASS puts
# the channels in the opposite order to CSS, and alpha 00 is opaque.
CAPTION_STYLES = {
    "clean": (
        "FontName=Segoe UI,FontSize=15,Bold=1,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&HA0000000,BorderStyle=3,Outline=2,Shadow=0,MarginV=60"
    ),
    "boxed": (
        "FontName=Segoe UI,FontSize=15,Bold=1,PrimaryColour=&H00FFFFFF,"
        "BackColour=&H80000000,OutlineColour=&H80000000,BorderStyle=4,"
        "Outline=6,Shadow=0,MarginV=60"
    ),
    "bold-yellow": (
        "FontName=Impact,FontSize=18,Bold=1,PrimaryColour=&H0000E5FF,"
        "OutlineColour=&HFF000000,BorderStyle=1,Outline=3,Shadow=1,MarginV=64"
    ),
    "neon": (
        "FontName=Segoe UI,FontSize=16,Bold=1,PrimaryColour=&H00F0FF00,"
        "OutlineColour=&HC0500000,BorderStyle=1,Outline=3,Shadow=2,MarginV=64"
    ),
}

# 1:1 and 4:5 sit between a reel and the original, and both do well in feeds
SHAPES = {
    "reels": (1080, 1920),
    "square": (1080, 1080),
    "portrait": (1080, 1350),
}


# Fonts that actually contain Myanmar glyphs, best first. Naming one matters:
# the caption styles ask for Segoe UI, which has none, and what happens then is
# fontconfig's choice rather than ours.
#
# Ordered by how they actually render, compared at caption size on a real
# subtitle: Myanmar Text sets its lines tight enough that the stacked marks of
# one line collide with the line above -- legible but visibly wrong, and the
# reason captions looked broken. Pyidaungsu leaves room for the stacks and is
# the cleanest of the three; Padauk Book is close behind with wider tracking.
# Captions per ffmpeg run. Each one costs an input and a filter link, and the
# command line is bounded; 40 leaves generous room under every platform's
# limit while keeping the number of re-encodes small.
PER_PASS = 40

MY_FONTS = ("Pyidaungsu", "Padauk Book", "Myanmar Text", "Noto Sans Myanmar")


def filmstrip(src: Path, dest: Path, count: int = 48, height: int = 56,
              cancel=None) -> Path:
    """
    One wide image of the whole video, sampled evenly.

    Trimming by typing a number means watching the video with a stopwatch to
    find where the copyright card ends. A strip of frames turns that into
    looking: the card is visibly a different picture from the film, so the
    seam is somewhere you can point at.
    """
    seconds = probe(src).duration or 0.0
    if seconds <= 0:
        raise MediaError("that video has no length to sample")
    count = max(8, count)
    # fps as a fraction, so the frames land evenly however long the video is
    args = [
        _tool("ffmpeg"), "-y", "-i", str(src),
        "-vf", f"fps={count}/{seconds:.6f},scale=-1:{height},tile={count}x1",
        "-frames:v", "1", "-q:v", "5", str(dest),
    ]
    _run(args, cancel=cancel)
    if not dest.exists():
        raise MediaError("the filmstrip came out empty")
    return dest


def burn_subtitles(src: Path, srt: Path, dest: Path, style: str = "clean",
                   cancel=None, lang: str = "") -> Path:
    """
    Burn an SRT into the picture, for feeds that autoplay muted.

    Latin only. libass does NOT shape Myanmar -- the claim that once stood here
    was never tested and is false. Rendering the same line through libass,
    drawtext and Pillow gives identical, identically wrong output: the glyphs
    are placed in storage order, so no font can fix it. Burmese goes through
    burn_caption_images() instead, drawn by Chromium, which does shape it.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # ffmpeg's filter parser needs the drive colon and backslashes escaped
    escaped = srt.as_posix().replace(":", r"\:")
    style = CAPTION_STYLES.get(style, CAPTION_STYLES["clean"])
    if lang == "my":
        # Say which font rather than leaving it to substitution
        style = re.sub(r"FontName=[^,]+", "FontName=" + MY_FONTS[0], style)
    _run([
        _tool("ffmpeg"), "-y", "-i", str(src),
        "-vf", f"subtitles='{escaped}':force_style='{style}'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-avoid_negative_ts", "make_zero", str(dest),
    ], cancel=cancel)
    return dest


def burn_caption_images(src: Path, rows: list[dict], dest: Path,
                        cancel=None, on_progress=None) -> Path:
    """
    Lay pre-rendered caption images over the picture.

    ffmpeg cannot shape Burmese. libass, drawtext and Pillow all draw the
    codepoints in storage order, so a vowel that belongs to the left of its
    consonant stays on the right -- tested against Pillow with shaping
    disabled, which produced identical output to both of the others. No font
    fixes that, because the fault is in the shaping, not the glyphs.

    Chromium does shape it correctly, which is why the thumbnail overlay has
    always looked right. So the captions are drawn there, sent here as
    transparent PNGs, and composited. ffmpeg never sees the text.

    Each image is shown only between its own timestamps, so one filter chain
    carries the whole subtitle track. Each row may carry x and y, fractions of
    the frame naming where its centre goes.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    usable = [r for r in rows if Path(r["path"]).exists()]
    if not usable:
        raise MediaError("there are no caption images to lay down")

    # Every caption is another -i and another link in the filter chain, and
    # Windows refuses a command line over 32,767 characters. That arrives at
    # about 128 captions -- perfectly reachable, since a long script chunks
    # into several captions per line -- and it arrives as OSError from
    # subprocess, not as anything this module raises, so it surfaced as an
    # Internal Server Error with nothing to act on. So lay them down in
    # passes, each pass reading the last one's output.
    if len(usable) > PER_PASS:
        step = src
        work = dest.parent / "_caption_passes"
        work.mkdir(parents=True, exist_ok=True)
        passes = -(-len(usable) // PER_PASS)
        try:
            for i, n in enumerate(range(0, len(usable), PER_PASS)):
                batch = usable[n:n + PER_PASS]
                last = n + PER_PASS >= len(usable)
                out = dest if last else work / f"pass_{n:04d}.mp4"
                # each pass covers its own slice of the whole job
                here = ((lambda k: lambda f: on_progress((k + f) / passes))(i)
                        if on_progress else None)
                burn_caption_images(step, batch, out, cancel=cancel,
                                    on_progress=here)
                if step is not src:
                    step.unlink(missing_ok=True)
                step = out
        finally:
            for leftover in work.glob("pass_*.mp4"):
                leftover.unlink(missing_ok=True)
            if not any(work.iterdir()):
                work.rmdir()
        return dest

    args = [_tool("ffmpeg"), "-y", "-i", str(src)]
    for r in usable:
        args += ["-i", str(r["path"])]

    chain = []
    last = "[0:v]"
    for i, r in enumerate(usable, start=1):
        out = f"[v{i}]"
        start, end = float(r["start"]), float(r["end"])
        # Where the caption sits, as a fraction of the frame -- the point the
        # user dragged it to, which is its centre. Clamped inside the picture
        # so a caption dragged to the edge is still wholly readable.
        fx = min(1.0, max(0.0, float(r.get("x", 0.5))))
        fy = min(1.0, max(0.0, float(r.get("y", 0.86))))
        place = (f"x='min(max({fx:.4f}*W-w/2,0),W-w)'"
                 f":y='min(max({fy:.4f}*H-h/2,0),H-h)'")
        chain.append(
            f"{last}[{i}:v]overlay={place}"
            f":enable='between(t,{start:.3f},{end:.3f})'{out}"
        )
        last = out

    args += [
        "-filter_complex", ";".join(chain),
        "-map", last, "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart", str(dest),
    ]
    _run(args, cancel=cancel, on_progress=on_progress,
         seconds=(probe(src).duration or 0.0) if on_progress else 0.0)
    return dest


def mux_narration(
    video: Path,
    clips: list[dict],
    dest: Path,
    original_volume: float = 0.25,
    narration_volume: float = 1.0,
    on_progress=None,
    speed: float = 1.0,
    reencode: bool = False,
    cancel=None,
) -> Path:
    """
    Lay spoken narration over a cut, keeping the original audio underneath.

    `original_volume` is the level the source audio is held at while the
    narration plays -- 0 silences it completely, 1 leaves it untouched. Each
    clip is delayed to the moment in the recap it belongs to, so the voice
    lands on the footage it describes.

    The video stream is copied, not re-encoded: only the audio changes, and
    re-encoding the picture again would cost minutes and a generation of
    quality for nothing.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    usable = [c for c in clips if Path(c["path"]).exists()]
    if not usable:
        raise MediaError("there is no narration audio to lay down")

    args = [_tool("ffmpeg"), "-y", "-i", str(video)]
    for c in usable:
        args += ["-i", str(c["path"])]

    chains = []
    labels = []
    has_original = probe(video).has_audio and original_volume > 0.001
    if has_original:
        chains.append(f"[0:a]volume={original_volume:.3f}[bg]")
        labels.append("[bg]")
    # atempo changes pace without changing pitch, but only within 0.5-2.0, so
    # anything outside that is clamped rather than silently mangled.
    speed = min(2.0, max(0.5, float(speed or 1.0)))
    tempo = "" if abs(speed - 1.0) < 0.01 else f"atempo={speed:.3f},"

    for i, c in enumerate(usable, start=1):
        delay = max(0, int(float(c.get("at") or 0) * 1000))
        # speed first, then the delay -- the delay is a position on the recap
        # timeline and must not be stretched along with the speech
        chains.append(
            f"[{i}:a]{tempo}adelay={delay}|{delay},volume={narration_volume:.3f}[n{i}]"
        )
        labels.append(f"[n{i}]")

    # normalize=0: amix otherwise divides every input by the number of inputs,
    # which makes the voice quieter the more lines the recap has
    chains.append(
        "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[aout]"
    )

    args += [
        "-filter_complex", ";".join(chains),
        "-map", "0:v",
        # Copying the video keeps its original timestamps, and a stream cut
        # from the middle of a recap starts on a negative DTS with reordered
        # frames around it. A player reconciles that by seeking, which is the
        # opening second jumping back on itself. For a short preview the
        # honest fix is to re-encode: seconds of work for a head that starts
        # cleanly at zero. The finished render still copies -- it is minutes
        # of video, and it is not the file being scrubbed.
        *(["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p"] if reencode else ["-c:v", "copy"]),
        "-map", "[aout]", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        # The cut's first video packet carries a negative DTS and the frames
        # around it arrive out of order, because the stream is copied rather
        # than re-encoded. Chromium reconciles that by seeking, which shows as
        # the opening second or two jumping back on itself before settling.
        # Shifting the timestamps so nothing starts before zero removes the
        # thing being reconciled.
        "-avoid_negative_ts", "make_zero", "-muxpreload", "0", "-muxdelay", "0",
        str(dest),
    ]
    _run(args, cancel=cancel, on_progress=on_progress,
         seconds=(probe(video).duration or 0.0) if on_progress else 0.0)
    return dest


def prepend_still(video: Path, picture: Path, seconds: float = 0.6,
                  cancel=None, on_progress=None) -> bool:
    """
    Put the thumbnail on the front of the video as real footage.

    Cover art inside the file is ignored by every social platform, and by
    Windows Explorer for mp4. What they do read is the picture itself: their
    cover pickers offer frames FROM THE VIDEO, and the one they offer first is
    the opening frame. A thumbnail that exists only as a separate file has to
    be attached by hand in each uploader; one that is also the first frame can
    be chosen on a phone, and is what the profile grid falls back to.

    Short is the point -- half a second reads as a title card, two seconds
    reads as a delay before the video starts.

    The whole file is re-encoded. Building a matching intro and concatenating
    with -c copy is the fast way and a fragile one: the intro has to agree
    with the video on resolution, frame rate, pixel format, timebase, aspect
    and profile, and when it does not the result is a file that plays wrong
    rather than one that fails. One encode is slower and always correct.
    """
    if not video.exists() or not picture.exists():
        return False
    seconds = max(0.1, min(5.0, float(seconds or 0)))

    shape = probe(video)
    out = video.with_name(video.stem + "_lead.mp4")
    try:
        _run([
            _tool("ffmpeg"), "-y",
            "-loop", "1", "-t", f"{seconds:.3f}", "-i", str(picture),
            "-i", str(video),
            # The still is scaled and padded into the video's own frame, so a
            # thumbnail saved at a different shape cannot stretch the picture.
            "-filter_complex",
            (f"[0:v]scale={shape.width}:{shape.height}:force_original_aspect_ratio=decrease,"
             f"pad={shape.width}:{shape.height}:-1:-1:color=black,"
             f"setsar=1,fps={shape.fps:.4f},format=yuv420p[lead];"
             "[1:v]setsar=1,format=yuv420p[body];"
             "[lead][body]concat=n=2:v=1:a=0[v];"
             # silence under the still, so the narration is not dragged
             # forward and the audio stays as long as the picture
             f"anullsrc=r=48000:cl=stereo,atrim=0:{seconds:.3f}[q];"
             "[q][1:a]concat=n=2:v=0:a=1[a]"),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(out),
        ], cancel=cancel, on_progress=on_progress,
            seconds=(shape.duration or 0.0) + seconds if on_progress else 0.0)
    except MediaError:
        out.unlink(missing_ok=True)
        return False       # the video without its cover frame beats no video

    if not out.exists() or out.stat().st_size < 1000:
        out.unlink(missing_ok=True)
        return False
    out.replace(video)
    return True


def set_cover(video: Path, picture: Path, cancel=None) -> bool:
    """
    Put the thumbnail inside the video file, as its cover art.

    A thumbnail sitting beside a video in a folder is a thumbnail somebody has
    to remember to upload. Carried in the file it survives being moved, copied
    and handed to someone else, and Windows and most players show it straight
    away -- so what you see in the folder is the thumbnail you chose rather
    than a frame the player picked.

    Both streams are copied, so this costs a remux rather than an encode:
    seconds on a file that took minutes to render.
    """
    if not video.exists() or not picture.exists():
        return False

    out = video.with_name(video.stem + "_cover.mp4")
    try:
        _run([
            _tool("ffmpeg"), "-y", "-i", str(video), "-i", str(picture),
            "-map", "0", "-map", "1",
            "-c", "copy", "-c:v:1", "mjpeg",
            # what makes a video stream a cover rather than a second picture
            "-disposition:v:1", "attached_pic",
            "-movflags", "+faststart", str(out),
        ], cancel=cancel)
    except MediaError:
        out.unlink(missing_ok=True)
        return False       # a missing cover is not a reason to lose the render

    if not out.exists() or out.stat().st_size < 1000:
        out.unlink(missing_ok=True)
        return False
    out.replace(video)
    return True


def to_wav(src: Path, dest: Path, rate: int = 24000, cancel=None) -> Path:
    """
    Convert any audio a user uploads into the mono WAV the mixer expects.

    Accepting an mp3 or an m4a and silently mixing it at the wrong rate would
    play the narration at the wrong speed, so everything is normalised here.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run([
        _tool("ffmpeg"), "-y", "-i", str(src),
        "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dest),
    ], timeout=300, cancel=cancel)
    return dest
