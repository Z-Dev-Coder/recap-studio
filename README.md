# Recap Studio — Desktop AI Multimedia & Voice Cloning Suite

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.11--3.14-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Electron](https://img.shields.io/badge/Electron.js-Desktop_App-47848F?style=for-the-badge&logo=electron&logoColor=white)](https://electronjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend_Service-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Gemini AI](https://img.shields.io/badge/Gemini_AI-Bilingual_Scripting-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev)
[![PyTorch](https://img.shields.io/badge/PyTorch-Neural_TTS-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org)

**Production desktop application that transforms raw video into a complete social media posting kit — automated transcription, bilingual AI scripting, highlight cuts, and self-hosted neural voice cloning, all at $0 cloud cost.**

</div>

---

## Overview

Recap Studio is the flagship component of **Toolbox**, a multi-process Electron desktop application housing three purpose-built personal productivity tools. It demonstrates end-to-end software engineering across process boundaries: an Electron.js frontend communicates via IPC with an asynchronous Python FastAPI microservice that orchestrates a full multimedia pipeline.

**Pipeline input → output:**

| Input | Output |
|-------|--------|
| Video URL or local file | Full narrated recap cut (`.mp4`) |
| — | Bilingual script (English + Burmese) |
| — | Automated subtitle tracks (`.srt`) |
| — | Thumbnail candidates (scored frames) |
| — | Hashtags, description, and posting copy |
| — | Zero-latency voice cloning narration audio |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Electron.js Frontend                   │
│  Renderer (HTML/JS) ←→ Preload bridge ←→ Main process   │
│          IPC channels for per-step streaming             │
└────────────────────┬────────────────────────────────────┘
                     │  HTTP + SSE (Server-Sent Events)
┌────────────────────▼────────────────────────────────────┐
│              Python FastAPI Microservice                  │
│                                                          │
│  pipeline.py  → Coordinates all steps, cancelable        │
│  video.py     → yt-dlp download, FFmpeg slicing/burn     │
│  script.py    → Gemini AI prompt engineering (EN + MY)   │
│  tts.py       → Google TTS cloud synthesis               │
│  localtts.py  → VoxCPM2 CUDA neural voice cloning        │
│  media.py     → All FFmpeg subprocess calls              │
└─────────────────────────────────────────────────────────┘
```

**Key architectural decisions:**
- **IPC + HTTP bridge**: Electron's main process proxies FastAPI SSE streams into IPC events, so the renderer receives real-time progress without polling.
- **Pluggable TTS provider**: A common TTS interface accepts either `tts.py` (Gemini Cloud) or `localtts.py` (self-hosted VoxCPM2 on CUDA), swapped via settings — zero application code changes.
- **Per-step rerunnability**: Each pipeline step persists its outputs. Any step can be rerun independently after editing, without redoing upstream work.
- **Tool registry pattern**: New tools are added by dropping a folder + a manifest entry — no changes to main process code.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Desktop Shell** | Electron.js (Node.js 22+) |
| **Backend Service** | Python FastAPI with async/await |
| **IPC & Streaming** | Electron IPC + HTTP Server-Sent Events (SSE) |
| **Speech-to-Text** | Faster-Whisper (local, runs offline) |
| **Script Generation** | Gemini AI (bilingual EN + Burmese prompting) |
| **Video Processing** | FFmpeg: slicing, audio ducking, subtitle burn |
| **Neural TTS / Voice Cloning** | VoxCPM2 (PyTorch CUDA acceleration) |
| **Video Download** | yt-dlp (1,750+ platform support) |
| **Screen Recording** | Electron desktopCapturer + MediaRecorder |
| **Language Support** | English + Burmese (Myanmar) |

---

## Toolbox: The Three Tools

### 🎬 Recap Studio
Video URL or local file in → full social media posting kit out. Automated Whisper transcription, Gemini-powered bilingual scripting, FFmpeg highlight extraction, thumbnail scoring, and self-hosted VoxCPM2 voice cloning. See [Using Recap Studio](#using-recap-studio).

### 🖥️ Screen Recorder
Records display, window, dragged region, webcam, or audio. Features a floating draggable camera bubble, global hotkeys (`F9` start/stop, `F10` pause, `F11` screenshot, `F8` show/hide camera), and camera-aware recording — where you position the bubble is where your face lands in the output.

### ⬇️ Video Downloader
yt-dlp behind a local web UI. Quality picker, audio-only mode, subtitle extraction, playlist support, and browser cookie import for private/age-gated content.

---

## Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| Windows | 10 or 11 | Recorder and installer are Windows-only |
| Node.js | 22+ | Tested on 24.15.0 |
| Python | 3.11–3.14 | Tested on 3.14.4 |
| FFmpeg | Any recent | Must be on `PATH`; `ffprobe` ships with it |
| Disk | ~2 GB | +~5 GB for local neural TTS (optional) |
| GPU | Optional | NVIDIA GPU required for VoxCPM2 voice cloning |

---

## Installation

### From the Built Installer

1. **Run** `Toolbox Setup 1.0.0.exe`
   - Windows SmartScreen may warn (unsigned build): click **More info → Run anyway**
2. **Choose install path** — avoid `C:\Program Files\` (write permissions needed for local TTS setup)
   - Recommended: `D:\Toolbox` or `C:\Users\<you>\AppData\Local\Programs\Toolbox`
3. **Install FFmpeg**
   ```powershell
   winget install Gyan.FFmpeg
   ```
   Restart Toolbox afterwards so it detects the updated `PATH`.
4. **Add Gemini API key** in Settings → API (free tier is sufficient)
5. **Optional: Enable local neural TTS** — see [Optional Extras](#optional-extras)

### From Source

```bash
# Clone and install Node dependencies
git clone https://github.com/Z-Dev-Coder/recap-studio.git
cd recap-studio
npm install

# Set up Python service environment
cd services/video-downloader
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Run in development mode
cd ../..
npm run dev
```

---

## Optional Extras

### Local Neural Voice Cloning (VoxCPM2)

Enables self-hosted narration at **$0 cloud cost**. Requires an NVIDIA GPU.

```bash
# In services/video-downloader (activate venv first)

# Install PyTorch with CUDA support
pip install torch==2.3.1 torchaudio==2.3.1 --index-url https://download.pytorch.org/whl/cu121

# Install VoxCPM2 TTS (without kaldifst to avoid CMake build issues)
pip install voxcpm --no-deps
pip install openai-whisper soundfile omegaconf pydantic
```

> **Note on kaldifst**: VoxCPM2's `wetext` dependency requires `kaldifst`, which needs CMake + C++ compiler on Python 3.13+. The app substitutes its own implementation — install with `--no-deps` as above.

### Local Speech-to-Text (Faster-Whisper)

```bash
pip install faster-whisper
```

---

## Using Recap Studio

1. **Input** — Paste a URL or select a local file
2. **Download** — Original video + real platform metadata fetched
3. **Transcript** — Platform captions used if available; Faster-Whisper runs locally as fallback
4. **Script** — Gemini AI generates title, description, hashtags, and beats in English *and* Burmese
5. **Recap Cut** — FFmpeg splices the highest-scoring beats into a compact highlight reel
6. **Thumbnails** — Frames extracted and scored by the script's own beat rankings
7. **Voice** — Each script line spoken by VoxCPM2 (or Gemini TTS); original audio is ducked underneath
8. **Files** — All outputs collected with plain-English labels, ready to drag into your uploader

Every step runs independently and reruns without invalidating others. Any running step can be cancelled.

### Voice Cloning

Upload a short recording of your own voice under **Your own voice**. VoxCPM2 clones it across any language — one recording narrates every line, including Burmese. Individual lines can be manually re-recorded; those recordings survive regeneration.

---

## Project Structure

```
main.js                       Electron main process — windows, hotkeys, IPC
preload.js                    Secure bridge exposing window.api to renderers
renderer/                     Dashboard shell, camera bubble, recording UI
modules/
  screen-recorder/            Screen recorder UI module
  manifest.json               Tool registry (add new tools here)
services/
  video-downloader/
    ytdl/
      downloader.py           yt-dlp wrapper
      web/                    FastAPI app + web UI
      recap/
        pipeline.py           Step orchestrator (each step independently cancelable)
        script.py             Prompt builder and beat planner
        video.py              FFmpeg splice, caption retime, subtitle burn
        tts.py                Gemini Cloud TTS synthesis
        localtts.py           VoxCPM2 CUDA local TTS
        media.py              All raw FFmpeg subprocess calls
```

---

## Output File Structure

Each recap run produces a self-contained folder ready to upload:

```
Downloads/RecapStudio/<title>-<id>/
  source.mp4                      Original, untouched
  recap_reels.mp4                 Highlight cut: no captions, no narration
  recap_reels_captioned.mp4       Highlight cut with captions burned in
  final_reels_my.mp4              Finished video with Burmese narration
  transcript.srt / .txt           Full original transcript
  transcript_my.srt               Translated transcript
  recap_script_my.srt             Recap script timed to the cut
  post_my.txt                     Title, description, and hashtags
  hashtags.txt
  thumbnail.png
  frames/                         Scored thumbnail candidates (no overlays)
  voice/                          Individual narration lines as audio files
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Buttons in Recap Studio do nothing | Press `Ctrl+Shift+I`, check Console for errors |
| `ffmpeg was not found on PATH` | `winget install Gyan.FFmpeg`, then restart app |
| `This video has no captions` | Install `faster-whisper` (see Optional Extras) |
| Gemini free-tier rate limit hit | App shows reset time; switch to local engine to avoid limits |
| Narration sounds like wrong language | Confirm model is `openbmb/VoxCPM2` (not smaller VoxCPM variants, which only support Chinese/English) |
| Narration is very slow | `torch.cuda.is_available()` is `False` — reinstall PyTorch with CUDA build |
| `Failed building wheel for kaldifst` | Use `pip install voxcpm --no-deps` and install dependencies separately |
| VoxCPM voice is too long for a line | Shorten the line in the Script tab; lines are auto-split on sentence punctuation |
| Installer is very large / build takes forever | Check `services/video-downloader/venv` — large newly-installed packages need an exclusion in `package.json` under `build.files` |

---

## License

MIT © Zaw Myo Tun
