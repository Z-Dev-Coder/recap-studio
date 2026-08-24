# Toolbox

An Electron app hosting three personal tools behind one dashboard: a screen
recorder, a video downloader, and **Recap Studio** — which turns a video into
everything needed to post it: a recap cut, a bilingual script, description,
hashtags, thumbnail, subtitles and spoken narration.

Built to grow: adding a tool is a folder plus a manifest entry, not a code
change.

---

## Contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Install the built app](#install-the-built-app)
- [Install from source](#install-from-source)
- [Optional extras](#optional-extras)
- [Getting a Gemini API key](#getting-a-gemini-api-key)
- [Running it](#running-it)
- [Building the installer](#building-the-installer)
- [Using Recap Studio](#using-recap-studio)
- [Where files go](#where-files-go)
- [Troubleshooting](#troubleshooting)
- [Layout](#layout)

---

## What it does

### Screen recorder
Records a display, a single window, a dragged region, the webcam alone, or
audio alone. A floating camera bubble sits on screen while you record — drag
it, resize it, hide it mid-take. The recording follows the bubble, so where you
put your face is where it lands in the video, and hiding it takes your face out
of the recording.

Hotkeys are global: `F9` start/stop, `F10` pause, `F11` screenshot, `F8`
hide/show the camera. All rebindable in Settings.

### Video downloader
yt-dlp behind a local web UI. YouTube, Facebook, TikTok, Instagram and around
1,750 other sites. Quality picker, audio-only, subtitles, playlists, and
cookies from your browser for private or age-gated content.

### Recap Studio
A link — or a file already on your PC — in, and a full posting kit out:

| Step | What it produces |
| --- | --- |
| Download | the source video and its real metadata |
| Transcript | platform captions when they exist, local Whisper when they don't |
| Script | beats, title, description, hashtags — in English **and** Burmese |
| Recap cut | the moments spliced together, any length from seconds to the full original |
| Frames | thumbnail candidates, ranked by the script's own scores |
| Voice | narration spoken over the cut, original audio held underneath |

Every step reruns on its own, everything is editable by hand, and anything
running can be stopped.

---

## Requirements

| | Version | Notes |
| --- | --- | --- |
| Windows | 10 or 11 | the recorder and installer are Windows-only |
| Node.js | 22+ | tested on 24.15.0 |
| Python | 3.11–3.14 | tested on 3.14.4 |
| ffmpeg | any recent | must be on `PATH`; ffprobe ships with it |
| Disk | ~2 GB | plus ~5 GB more for local narration |
| GPU | optional | an NVIDIA card is what makes local narration usable |

---

## Install the built app

Use this if you just want to run it. To develop it, or to build the installer
yourself, see [Install from source](#install-from-source).

### Step 1 — Run the installer

Double-click **`Toolbox Setup 1.0.0.exe`**.

Windows SmartScreen will warn, because the build is unsigned:
**More info → Run anyway**.

### Step 2 — Choose where it goes

This step matters more than it looks.

| Location | Works? |
| --- | --- |
| `D:\Toolbox` | ✅ recommended |
| `C:\Users\<you>\AppData\Local\Programs\Toolbox` | ✅ the default |
| `C:\Program Files\Toolbox` | ⚠️ avoid |

**Do not install into Program Files.** Local narration is a `pip install` into
the app's own bundled environment, and Program Files cannot be written to
without administrator rights — so an app installed there can never gain that
feature. Anywhere in your own user folder, or a data drive like `D:\`, stays
writable.

Type a full path such as `D:\Toolbox`, or use **Browse**. The **Install**
button stays greyed out until the path is a valid absolute one — clearing the
field disables it.

It needs about **711 MB** installed (the installer itself is ~190 MB
compressed).

### Step 3 — Install ffmpeg

The recap cut, the thumbnails and the screen recorder's conversion all need
it. In PowerShell:

```powershell
winget install Gyan.FFmpeg
```

Restart Toolbox afterwards so it picks up the new `PATH`.

### Step 4 — Add a Gemini API key

See [Getting a Gemini API key](#getting-a-gemini-api-key). Free tier is enough.

### Step 5 — Optional: enable local narration

> **Set up the service environment first.** Run `setup-external.cmd` from the
> source checkout, or the commands below against
> `%APPDATA%\Toolbox\service`. That folder sits outside the app, so updating
> Toolbox never deletes it — an environment inside the app folder is wiped on
> every update, which also makes the update take minutes.


The installer deliberately leaves out the local-narration stack — it is 4.3 GB
of PyTorch for a feature that downloads its own model as well. To turn it on,
point these at wherever you installed:

```powershell
cd "D:\Toolbox\resources\app\services\video-downloader"

# 1. VoxCPM itself, WITHOUT its dependency chain
.\venv\Scripts\python.exe -m pip install voxcpm --no-deps

# 2. the dependencies it actually needs
.\venv\Scripts\python.exe -m pip install torch torchaudio transformers safetensors huggingface-hub soundfile librosa einops inflect addict tqdm pydantic

# 3. swap in the CUDA build so it runs on the GPU
.\venv\Scripts\python.exe -m pip install --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu126
```

> **Why `--no-deps`.** A plain `pip install voxcpm` fails on Python 3.13+
> with *"Failed building wheel for kaldifst"*. `kaldifst` publishes no
> wheel for these versions and building it needs CMake and a C++ compiler.
> It arrives through `wetext`, which VoxCPM uses in exactly one place — to
> expand Chinese and English numbers into words. Burmese narration never
> touches it, and the app supplies a pass-through stand-in, so skipping the
> chain costs nothing but the number formatting in English narration.

About 4.5 GB and ten minutes. **No administrator rights are needed** as long as
you did not install into Program Files. Model weights are cached in your user
profile, so they are shared with any other copy and never downloaded twice.

Check it took:

```powershell
.\venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"
```

`True` means narration runs on your GPU at roughly a third of realtime.
`False` means the CPU build got installed, which is ~100x slower and not
usable — see the note about matching the CUDA channel in
[Optional extras](#optional-extras).

> Upgrading? Install the new version over the old one, then uninstall any
> earlier copy in a different folder so you do not run the stale one by
> mistake.

---

## Install from source

### Step 1 — Install the prerequisites

In PowerShell:

```powershell
winget install OpenJS.NodeJS
winget install Python.Python.3.12
winget install Gyan.FFmpeg
```

Close and reopen PowerShell so the new `PATH` takes effect, then check all
three are visible:

```powershell
node -v
python -V
ffmpeg -version
```

Each should print a version. If `ffmpeg` is missing the recap cut and the
thumbnails cannot work, so fix that before going on.

### Step 2 — Get the code

```powershell
git clone https://github.com/Z-Dev-Coder/recap-studio.git
cd recap-studio
```

### Step 3 — Install the app's dependencies

```powershell
npm install
```

A couple of minutes; it downloads Electron.

### Step 4 — Set up the Python service

Recap Studio and the downloader run on a small local Python service. It gets
its own virtual environment, so nothing touches your system Python:

```powershell
cd services\video-downloader
.\setup.cmd
cd ..\..
```

This creates `venv\`, upgrades pip and installs the requirements. It warns you
if ffmpeg is missing.

### Step 5 — Start it

```powershell
npm start
```

The dashboard opens with both tools listed. **This is enough to record your
screen and download videos.** Recap Studio needs an API key (Step 6) and
benefits from the optional extras below.

### Step 6 — Add a Gemini API key

See [Getting a Gemini API key](#getting-a-gemini-api-key).

---

## Optional extras

Each of these goes into the service's venv. None is required for the app to
start — every feature detects its own absence and prints the command you need.

Run them from wherever the service lives:

| How you installed | Folder |
| --- | --- |
| Built app | `<install folder>\resources\app\services\video-downloader` |
| From source | `services\video-downloader` |

### Transcribing videos that have no captions

Most Facebook, TikTok and Instagram videos have none. This transcribes them
locally:

```powershell
venv\Scripts\python.exe -m pip install faster-whisper
```

First use downloads a model (~500 MB for the `small` default). Size is
selectable in Settings.

### Reading the source page for context

Gives the script writer the video's real tags and top comments, so the recap
knows what the audience actually reacted to:

```powershell
venv\Scripts\python.exe -m pip install playwright
venv\Scripts\python.exe -m playwright install chromium
```

### Local narration — unlimited, free, and the practical way to get Burmese

Gemini's free tier speaks roughly three lines a minute and a recap is a dozen
lines. VoxCPM runs on your own machine with no quota at all, supports Burmese,
and can clone a voice from a few seconds of audio.

```powershell
# VoxCPM without its dependency chain -- see the note below
venv\Scripts\python.exe -m pip install voxcpm --no-deps

# what it actually needs
venv\Scripts\python.exe -m pip install torch torchaudio transformers safetensors huggingface-hub soundfile librosa einops inflect addict tqdm pydantic
```

> **`--no-deps` is not optional.** A plain `pip install voxcpm` fails on
> Python 3.13+ with *"Failed building wheel for kaldifst"* — no wheel is
> published and building it needs CMake and a C++ compiler. It comes in
> through `wetext`, which VoxCPM uses only to expand Chinese and English
> numbers into words; the app supplies a pass-through stand-in for it.

**On CPU this is roughly 100× slower than realtime — not usable.** With an
NVIDIA card, install the CUDA build of PyTorch. Match the channel to your
torch version — `cu126` carries 2.13:

```powershell
venv\Scripts\python.exe -m pip install --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu126
```

Confirm it took:

```powershell
venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"
```

`True` means narration runs at about a third of realtime instead. On a 6 GB
GTX 1660 Ti, ten lines take roughly five minutes.

> **Only VoxCPM2 is offered, deliberately.** `VoxCPM-0.5B` and `VoxCPM1.5` are
> a third the size and three times faster, but they are trained on Chinese and
> English alone — and handed Burmese they do not fail, they speak fluent
> Chinese-sounding nonsense. A faster model that silently produces the wrong
> language is not a choice worth offering, so the app does not offer it.

---

## Getting a Gemini API key

Recap Studio uses Google Gemini to write the script, description and hashtags,
and optionally to speak them. The free tier is enough.

1. Go to <https://aistudio.google.com/apikey>
2. Sign in and click **Create API key**
3. Copy it
4. In Recap Studio open **Settings** and paste it into **Gemini API key**
5. Click **Load** beside Model to list what your key can use, or leave it on
   *choose automatically*

The key is stored locally in `Downloads\RecapStudio\settings.json`. It is never
committed — `.gitignore` excludes it.

> Google retires models from under new keys. If generation fails with "model is
> not available for this key", the error names one that works; pick it in
> Settings.

---

## Running it

```powershell
npm start
```

To reach Recap Studio: dashboard → **Video Downloader** → **Recap Studio** in
the header.

The app starts the Python service on a free port it picks at launch, so there
is no fixed URL to bookmark. To run the service on its own instead — useful
for debugging, and it opens a browser tab:

```powershell
cd services\video-downloader
.\ytdl-ui.cmd
```

That one uses port 8756: <http://127.0.0.1:8756/recap>.

---

## Building the installer

```powershell
npm run dist
```

Two files land in `dist\`:

- **`Toolbox Setup 1.0.0.exe`** — installer, ~190 MB, with Start-menu and
  desktop shortcuts
- **`Toolbox 1.0.0.exe`** — portable, no install

The installer deliberately **excludes the local-narration stack** (PyTorch and
friends, ~4.3 GB); it would make the download unusably large for a feature that
fetches its own model anyway. Everything else is bundled, including the Python
service. To enable local narration in an installed copy, run the
`pip install voxcpm` step against the installed venv.

Windows SmartScreen warns on first run because the build is unsigned:
**More info → Run anyway**.

---

## Using Recap Studio

1. **New recap** — paste a link, or **Choose file** for a video already on your
   PC. Pick **Short / Reel** or **Long form**.
2. The pipeline runs. Watch the step chips; **Stop** cancels anything
   mid-flight, including a download or an encode.
3. **Recap cut** — the length bar starts at the full original. Drag it down and
   the weakest moments give up their seconds first, so what remains is the best
   of it. Then **Apply & rebuild**.
4. **Script** — every line is editable, with the moment it plays over and a
   1–10 score for why it earned its place. Edits save themselves.
5. **Title & tags** — editable, autosaving.
6. **Voice** — pick the engine, the language (or **Both**, which renders one
   video per language), and a voice. Click any voice to hear it. **Suggest for
   this video** picks one to match the content. Set how loud the original audio
   stays underneath *before* generating.
7. **Thumbnail** — frames ranked by the script's scores; drop text on one.
   Burmese shapes correctly here.
8. **Files** — every raw output, downloadable.

### Using your own voice

Upload a few seconds of yourself speaking under **Your own voice**. VoxCPM
clones it across languages, so one recording narrates every line — including in
Burmese. Telling it what the clip says sharpens the copy.

You can also upload your own recording for any single line. Those survive a
regenerate and cost no quota.

---

## Where files go

Recordings go to `Videos\ScreenRecorder`, downloads to your Downloads folder.
Each recap gets its own folder:

```
Downloads\RecapStudio\<title>-<id>\
  source.mp4                             the original, untouched
  recap_reels.mp4                        the cut: no captions, no narration
  recap_reels_captioned.mp4              the cut with captions burned in
  final_reels_my.mp4                     the finished video, Burmese narration
  transcript.srt / .txt                  what was said in the original
  transcript_my.srt                      that transcript, translated
  recap_script_my.srt                    the recap script, timed to the recap
  recap_script_my_original_timing.srt    the same, timed to the source
  post_my.txt                            title, description and hashtags
  hashtags.txt
  thumbnail.png
  frames\                                thumbnail candidates, no text on them
  voice\                                 each spoken line on its own
```

The folder is the deliverable — open it and drag files straight into an
uploader. The **Files** tab in the app lists all of it with plain-English
labels.

---

## Troubleshooting

**Nothing happens when I click anything in Recap Studio**
The page's script failed to load. Press `Ctrl+Shift+I` and read the Console
tab — a single error there stops every button on the page.

**"ffmpeg was not found on PATH"**
`winget install Gyan.FFmpeg`, then restart the app so it picks up the new
`PATH`.

**"This video has no captions"**
Install `faster-whisper` (above). Most non-YouTube videos need it.

**"Gemini free-tier limit reached"**
The app reads Google's own reset time and tells you when to retry. Narration is
the heavy user — switch to the local engine to stop hitting the limit at all.
Lines already spoken are kept, so a retry resumes rather than starting over.

**Narration sounds like the wrong language**
Check the model is `openbmb/VoxCPM2`. The smaller VoxCPM models are trained on
Chinese and English alone and will speak anything else as Chinese-sounding
nonsense without complaining. The app only offers VoxCPM2 for that reason, but
a project saved before that change may still name one of them.

**Narration is impossibly slow**
`torch.cuda.is_available()` is `False` — you have the CPU build of PyTorch.
Install the CUDA build (above).

**"Failed building wheel for kaldifst" when installing voxcpm**
Use `pip install voxcpm --no-deps` and then install its dependencies
separately, as in [Optional extras](#optional-extras). `kaldifst` has no
wheel for Python 3.13+ and needs CMake and a C++ compiler to build. VoxCPM
does not import it — it arrives through `wetext`, which the app stands in
for.

**"VoxCPM could not speak this part"**
A line was too long for the model's context. Lines are split on sentence
punctuation automatically; if it persists, shorten that line in the Script tab.

**The narration is much longer than the video**
The script writes to the clip length, so this means the beats were rewritten
without rebuilding the cut. Rebuild the cut, then regenerate the voice.

**The installer is enormous, or the build takes forever**
Something large landed in `services\video-downloader\venv`. The exclusion list
lives in `package.json` under `build.files`. Anything newly installed and heavy
belongs there — but check nothing shipped imports it at module level, or the
app will fail to start.

---

## Layout

```
main.js                    Electron main process: windows, hotkeys, IPC
preload.js                 the bridge the pages use (exposes window.api)
renderer/                  dashboard shell, camera bubble, recording panel
modules/screen-recorder/   the recorder UI
modules/manifest.json      the tool registry
services/video-downloader/ the Python service
  ytdl/downloader.py         yt-dlp wrapper
  ytdl/web/                  FastAPI app and the web UI
  ytdl/recap/                Recap Studio
    pipeline.py                the steps, each runnable alone
    script.py                  prompt building and beat planning
    video.py                   splicing and subtitle retiming
    tts.py / localtts.py       cloud and local narration
    media.py                   every ffmpeg call
```
