import os
from pathlib import Path
from pydantic import BaseModel

class Config(BaseModel):
    # Providers
    llm_provider: str = os.getenv("LLM_PROVIDER", "gemini")
    llm_model: str = os.getenv("LLM_MODEL", "")
    vision_provider: str = os.getenv("VISION_PROVIDER", "gemini")
    vision_model: str = os.getenv("VISION_MODEL", "")
    tts_provider: str = os.getenv("TTS_PROVIDER", "local")
    tts_model: str = os.getenv("TTS_MODEL", "")
    tts_voice: str = os.getenv("TTS_VOICE", "Kore")
    whisper_model: str = os.getenv("WHISPER_MODEL", "small")
    whisper_device: str = os.getenv("WHISPER_DEVICE", "auto")
    
    # Paths
    ffmpeg_path: str = os.getenv("FFMPEG_PATH", "ffmpeg")
    default_language: str = os.getenv("DEFAULT_LANGUAGE", "my")
    default_target_duration: float = float(os.getenv("DEFAULT_TARGET_DURATION", 300))
    cache_enabled: bool = os.getenv("CACHE_ENABLED", "true").lower() == "true"

    # API Keys
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")

def load_env(env_path: Path):
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            os.environ[key.strip()] = val.strip()

# Attempt to load .env from the root
load_env(Path(__file__).parent.parent.parent / ".env")
settings = Config()
