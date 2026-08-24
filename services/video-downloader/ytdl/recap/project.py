"""
A recap project: one folder on disk holding every asset for one post.

Everything lives in that folder as ordinary files -- the source video, both
subtitle sets, the recap cut, the thumbnail, the description -- so the folder
itself is the deliverable and can be opened in Explorer and dragged into any
uploader. project.json is the index over those files.

Each step is independently re-runnable, and every generated field is written
back to project.json when the user edits it, so a regenerate never silently
discards hand edits to a different field.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

STEPS = ("source", "transcript", "script", "video", "thumbnail", "voice")


def slugify(text: str, limit: int = 48) -> str:
    text = re.sub(r"[^\w\s-]", "", text or "", flags=re.UNICODE).strip()
    text = re.sub(r"[\s_-]+", "-", text)
    return (text[:limit] or "recap").strip("-").lower()


@dataclass
class Step:
    status: str = "idle"        # idle | running | done | error
    message: str = ""
    error: str = ""
    updated: float = 0.0


@dataclass
class Project:
    id: str
    dir: Path
    url: str = ""
    source_file: str = ""         # a video the user supplied instead of a link
    title: str = ""
    uploader: str = ""
    duration: float = 0.0
    mode: str = "reels"           # reels | long
    framing: str = "blur"         # blur bars, or crop the sides away
    shape: str = ""               # reels | square | portrait; blank follows mode
    burn_captions: bool = False   # for feeds that autoplay muted
    caption_style: str = "clean"
    caption_lang: str = ""        # blank follows the narration language
    language: str = "en"          # en | my  (which one the UI is showing)
    target_seconds: float = 0.0   # what the script step plans for
    cut_seconds: float = 0.0      # the length slider; 0 means the full original
    created: float = field(default_factory=time.time)

    # generated, all hand-editable afterwards
    transcript: list[dict] = field(default_factory=list)
    transcript_language: str = ""
    source_description: str = ""
    source_tags: list[str] = field(default_factory=list)
    beats: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    titles: dict = field(default_factory=lambda: {"en": "", "my": ""})
    description: dict = field(default_factory=lambda: {"en": "", "my": ""})
    hashtags: list[str] = field(default_factory=list)
    thumbnail_text: dict = field(default_factory=lambda: {"en": "", "my": ""})
    thumbnail_candidates: list[dict] = field(default_factory=list)
    coverage: float = 0.0
    video_type: str = ""          # podcast, tutorial, cartoon ... shapes the writing
    pacing: str = ""
    hook: dict = field(default_factory=lambda: {"en": "", "my": ""})

    # narration
    voice_engine: str = "gemini"    # gemini | voxcpm (local, no quota)
    local_model: str = ""           # which VoxCPM size; blank means the default
    voice_reference: str = ""       # a clip to clone, for the local engine
    voice_reference_text: str = ""
    voice_lang: str = "my"          # the language shown in the UI
    voice_langs: list = field(default_factory=list)   # every language to render
    voice_name: str = "Kore"
    voice_style: str = ""
    voice_reason: str = ""          # why the suggestion picked it
    original_volume: float = 0.25   # how loud the source stays under the voice
    narration_volume: float = 1.0
    narration: list[dict] = field(default_factory=list)

    steps: dict = field(default_factory=lambda: {s: Step() for s in STEPS})
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ---------------------------------------------------------------- paths
    @property
    def source_path(self) -> Path:
        return self.dir / "source.mp4"

    @property
    def recap_path(self) -> Path:
        return self.dir / f"recap_{self.mode}.mp4"

    @property
    def captioned_path(self) -> Path:
        """The cut with captions burned in, kept beside the clean one."""
        return self.dir / f"recap_{self.mode}_captioned.mp4"

    @property
    def final_path(self) -> Path:
        """The deliverable: the cut with narration over it."""
        return self.dir / f"final_{self.mode}_{self.voice_lang}.mp4"

    def finals(self) -> dict:
        """Which narrated videos exist, by language."""
        out = {}
        for code in ("en", "my"):
            path = self.dir / f"final_{self.mode}_{code}.mp4"
            if path.exists():
                out[code] = path.name
        return out

    @property
    def voice_dir(self) -> Path:
        return self.dir / "voice"

    @property
    def thumbnail_path(self) -> Path:
        return self.dir / "thumbnail.png"

    @property
    def frames_dir(self) -> Path:
        return self.dir / "frames"

    def json_path(self) -> Path:
        return self.dir / "project.json"

    # ---------------------------------------------------------------- state
    def mark(self, step: str, status: str, message: str = "", error: str = "") -> None:
        with self._lock:
            self.steps[step] = Step(
                status=status, message=message, error=error, updated=time.time()
            )
        self.save()

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "dir": str(self.dir),
            "url": self.url,
            "source_file": self.source_file,
            "title": self.title,
            "uploader": self.uploader,
            "duration": self.duration,
            "mode": self.mode,
            "framing": self.framing,
            "shape": self.shape,
            "burn_captions": self.burn_captions,
            "caption_style": self.caption_style,
            "caption_lang": self.caption_lang,
            "language": self.language,
            "target_seconds": self.target_seconds,
            "cut_seconds": self.cut_seconds,
            "created": self.created,
            "transcript": self.transcript,
            "transcript_language": self.transcript_language,
            "source_description": self.source_description,
            "source_tags": self.source_tags,
            "beats": self.beats,
            "timeline": self.timeline,
            "titles": self.titles,
            "description": self.description,
            "hashtags": self.hashtags,
            "thumbnail_text": self.thumbnail_text,
            "thumbnail_candidates": self.thumbnail_candidates,
            "coverage": self.coverage,
            "video_type": self.video_type,
            "pacing": self.pacing,
            "hook": self.hook,
            "voice_engine": self.voice_engine,
            "local_model": self.local_model,
            "voice_reference": self.voice_reference,
            "voice_reference_text": self.voice_reference_text,
            "voice_lang": self.voice_lang,
            "voice_langs": self.voice_langs,
            "finals": self.finals(),
            "voice_name": self.voice_name,
            "voice_style": self.voice_style,
            "voice_reason": self.voice_reason,
            "original_volume": self.original_volume,
            "narration_volume": self.narration_volume,
            "narration": self.narration,
            "has_final": self.final_path.exists(),
            "steps": {k: asdict(v) for k, v in self.steps.items()},
            "has_source": self.source_path.exists(),
            "has_recap": self.recap_path.exists(),
            "has_captioned": self.captioned_path.exists(),
            "has_thumbnail": self.thumbnail_path.exists(),
        }

    # ---------------------------------------------------------------- disk
    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = self.snapshot()
        data.pop("has_source", None)
        data.pop("has_recap", None)
        data.pop("has_thumbnail", None)
        data.pop("has_final", None)
        data.pop("has_captioned", None)
        tmp = self.json_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.json_path())

    @classmethod
    def load(cls, folder: Path) -> "Project":
        data = json.loads((folder / "project.json").read_text(encoding="utf-8"))
        saved = data.get("steps") or {}
        steps = {}
        for name in STEPS:
            row = saved.get(name) or {}
            # ignore any key this version does not know about
            steps[name] = Step(**{
                k: v for k, v in row.items()
                if k in ("status", "message", "error", "updated")
            })
        p = cls(
            id=data.get("id") or folder.name,
            dir=folder,
            url=data.get("url", ""),
            source_file=data.get("source_file", ""),
            title=data.get("title", ""),
            uploader=data.get("uploader", ""),
            duration=float(data.get("duration") or 0),
            mode=data.get("mode", "reels"),
            framing=data.get("framing", "blur"),
            shape=data.get("shape", ""),
            burn_captions=bool(data.get("burn_captions", False)),
            caption_style=data.get("caption_style", "clean"),
            caption_lang=data.get("caption_lang", ""),
            language=data.get("language", "en"),
            target_seconds=float(data.get("target_seconds") or 0),
            cut_seconds=float(data.get("cut_seconds") or 0),
            created=float(data.get("created") or time.time()),
            transcript=data.get("transcript") or [],
            transcript_language=data.get("transcript_language", ""),
            source_description=data.get("source_description", ""),
            source_tags=data.get("source_tags") or [],
            beats=data.get("beats") or [],
            timeline=data.get("timeline") or [],
            titles=data.get("titles") or {"en": "", "my": ""},
            description=data.get("description") or {"en": "", "my": ""},
            hashtags=data.get("hashtags") or [],
            thumbnail_text=data.get("thumbnail_text") or {"en": "", "my": ""},
            thumbnail_candidates=data.get("thumbnail_candidates") or [],
            coverage=float(data.get("coverage") or 0),
            video_type=data.get("video_type", ""),
            pacing=data.get("pacing", ""),
            hook=data.get("hook") or {"en": "", "my": ""},
            voice_engine=data.get("voice_engine", "gemini"),
            local_model=data.get("local_model", ""),
            voice_reference=data.get("voice_reference", ""),
            voice_reference_text=data.get("voice_reference_text", ""),
            voice_lang=data.get("voice_lang", "my"),
            voice_langs=data.get("voice_langs") or [],
            voice_name=data.get("voice_name", "Kore"),
            voice_style=data.get("voice_style", ""),
            voice_reason=data.get("voice_reason", ""),
            original_volume=float(data.get("original_volume", 0.25)),
            narration_volume=float(data.get("narration_volume", 1.0)),
            narration=data.get("narration") or [],
        )
        p.steps = steps
        return p


class Store:
    """Every project under one root, kept in memory and mirrored to disk."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._projects: dict[str, Project] = {}
        self._lock = threading.Lock()
        self._load_existing()

    def _load_existing(self) -> None:
        for folder in sorted(self.root.iterdir(), reverse=True):
            if not (folder / "project.json").exists():
                continue
            try:
                p = Project.load(folder)
                # a step interrupted by a restart is not still running
                for name, st in p.steps.items():
                    if st.status == "running":
                        p.steps[name] = Step(status="error", error="interrupted by restart")
                self._projects[p.id] = p
            except Exception:      # noqa: BLE001 - a broken folder must not hide the rest
                continue

    def create(self, url: str, title: str = "") -> Project:
        pid = uuid.uuid4().hex[:10]
        folder = self.root / f"{slugify(title or url)}-{pid}"
        folder.mkdir(parents=True, exist_ok=True)
        p = Project(id=pid, dir=folder, url=url, title=title)
        p.save()
        with self._lock:
            self._projects[pid] = p
        return p

    def get(self, pid: str) -> Project | None:
        with self._lock:
            return self._projects.get(pid)

    def all(self) -> list[dict]:
        with self._lock:
            items = sorted(self._projects.values(), key=lambda p: p.created, reverse=True)
        return [p.snapshot() for p in items]

    def delete(self, pid: str) -> bool:
        with self._lock:
            p = self._projects.pop(pid, None)
        if not p:
            return False
        shutil.rmtree(p.dir, ignore_errors=True)
        return True
