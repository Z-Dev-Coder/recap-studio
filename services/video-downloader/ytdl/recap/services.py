from abc import ABC, abstractmethod
from typing import Any, Callable

class TTSService(ABC):
    @abstractmethod
    def narrate(self, timeline: list[dict], out_dir: str, lang: str = "en", voice: str = "Kore", **kwargs) -> list[dict]:
        """
        Speak every line of the recap, one clip per beat.
        Returns a list of dictionaries detailing the generated clips.
        """
        pass

    @abstractmethod
    def release(self) -> None:
        """Releases any hardware resources (e.g., GPU memory)."""
        pass

    @abstractmethod
    def preview(self, text: str, voice: str, **kwargs) -> bytes:
        """Returns raw WAV bytes for a short sample preview."""
        pass

class LLMService(ABC):
    @abstractmethod
    def generate(self, prompt: str, schema: dict | None = None, **kwargs) -> Any:
        """
        Generates text or structured JSON from the LLM.
        """
        pass

class VisionService(ABC):
    @abstractmethod
    def analyze_frames(self, frames: list[bytes], prompt: str, **kwargs) -> Any:
        """
        Analyzes video frames.
        """
        pass

def get_llm_service(api_key: str, model: str = "") -> LLMService:
    from .service_impls import LLMServiceFactory
    return LLMServiceFactory.get(api_key, model)

def get_tts_service(engine: str = "voxcpm", api_key: str = "") -> TTSService:
    from .service_impls import TTSServiceFactory
    return TTSServiceFactory.get(engine, api_key)

def get_vision_service(api_key: str, model: str = "") -> VisionService:
    from .service_impls import VisionServiceFactory
    return VisionServiceFactory.get(api_key, model)
