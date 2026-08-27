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


def _run(args: list[str], timeout: int = 3600, cancel=None) -> str:
    """
    Run a tool to completion, or until the user stops it.

    With a `cancel` event this polls instead of blocking, so a Stop press
    kills the encoder within a fraction of a second rather than after the
    several minutes a long re-encode would otherwise take.
    """
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
        try:
            while popen.poll() is None:
                if cancel.is_set():
                    popen.kill()
                    popen.wait(timeout=10)
                    raise Cancelled()
                if time.monotonic() > deadline:
                    popen.kill()
                    raise MediaError(f"{args[0]} timed out")
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


def cut(src: Path, dest: Path, start: float, end: float, vertical: bool = False,
        cancel=None, framing: str = "blur") -> Path:
    """
    Copy one segment out of `src`, re-encoding so the joins are frame-exact.

    Stream copying would be faster but can only cut on keyframes, which slides
    every clip boundary by up to several seconds -- fatal when the whole point
    is that the clip matches the line of script being spoken over it.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.2, end - start)

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

    _run([
        _tool("ffmpeg"), "-y",
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
        "-filter_complex" if ((vertical or shape) and framing != "crop") else "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30",
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
            "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(dest),
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
MY_FONTS = ("Pyidaungsu", "Padauk Book", "Myanmar Text", "Noto Sans Myanmar")


def burn_subtitles(src: Path, srt: Path, dest: Path, style: str = "clean",
                   cancel=None, lang: str = "") -> Path:
    """
    Burn an SRT into the picture, for feeds that autoplay muted.

    Burmese shapes correctly here even though it does not in Pillow: libass
    goes through HarfBuzz, which reorders Myanmar glyph clusters properly.
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
        "-pix_fmt", "yuv420p", "-c:a", "copy", str(dest),
    ], cancel=cancel)
    return dest


def mux_narration(
    video: Path,
    clips: list[dict],
    dest: Path,
    original_volume: float = 0.25,
    narration_volume: float = 1.0,
    speed: float = 1.0,
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
        "-map", "0:v", "-c:v", "copy",
        "-map", "[aout]", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(dest),
    ]
    _run(args, cancel=cancel)
    return dest


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
