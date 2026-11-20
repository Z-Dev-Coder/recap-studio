from typing import Any
from pathlib import Path
from .services import TTSService, LLMService, VisionService

class GeminiLLM(LLMService):
    def __init__(self, api_key: str, model: str = ""):
        from .gemini import Gemini
        self.client = Gemini(api_key, model=model)

    def generate(self, prompt: str, schema: dict | None = None, **kwargs) -> Any:
        return self.client.generate_json(prompt, schema or {}, **kwargs)

class GeminiVision(VisionService):
    def __init__(self, api_key: str, model: str = ""):
        from .gemini import Gemini
        self.client = Gemini(api_key, model=model)

    def analyze_frames(self, frames: list[bytes], prompt: str, **kwargs) -> Any:
        images = [("image/jpeg", frame) for frame in frames]
        return self.client.generate_json(prompt, schema=kwargs.get("schema") or {}, images=images, **kwargs)

class LocalTTS(TTSService):
    def narrate(self, timeline: list[dict], out_dir: str, lang: str = "en", voice: str = "Kore", **kwargs) -> list[dict]:
        from .tts import narrate
        return narrate(api_key=kwargs.get("api_key", ""), timeline=timeline, out_dir=Path(out_dir), lang=lang, voice=voice, engine="voxcpm", **kwargs)

    def release(self) -> None:
        from .localtts import release
        release()

    def preview(self, text: str, voice: str, **kwargs) -> bytes:
        from .localtts import speak
        return speak(text, **kwargs)

class GeminiTTS(TTSService):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def narrate(self, timeline: list[dict], out_dir: str, lang: str = "en", voice: str = "Kore", **kwargs) -> list[dict]:
        from .tts import narrate
        return narrate(api_key=self.api_key, timeline=timeline, out_dir=Path(out_dir), lang=lang, voice=voice, engine="gemini", **kwargs)

    def release(self) -> None:
        pass

    def preview(self, text: str, voice: str, **kwargs) -> bytes:
        from .tts import preview
        return preview(self.api_key, voice, text=text, **kwargs)

class LLMServiceFactory:
    @staticmethod
    def get(api_key: str, model: str = "") -> LLMService:
        return GeminiLLM(api_key, model)

class VisionServiceFactory:
    @staticmethod
    def get(api_key: str, model: str = "") -> VisionService:
        return GeminiVision(api_key, model)

class TTSServiceFactory:
    @staticmethod
    def get(engine: str = "voxcpm", api_key: str = "") -> TTSService:
        if engine == "voxcpm":
            return LocalTTS()
        return GeminiTTS(api_key)
