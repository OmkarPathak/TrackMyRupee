"""Modular AI-provider layer. Providers are only used when their API key is set
in the environment (never in the browser). Without keys the studio falls back
to copy-ready prompts + manual uploads. NOTE: these adapters were written to the
providers' public HTTP APIs but could NOT be live-tested from the build
environment (no keys, restricted network). Model names are env-configurable."""
import base64, os
import requests


class Provider:
    name = "base"; kinds = ()
    env = ()
    def available(self): return all(os.environ.get(k) for k in self.env)


class OpenAIImage(Provider):
    name = "openai-image"; kinds = ("image",); env = ("OPENAI_API_KEY",)
    def image(self, prompt, size="1024x1536"):
        r = requests.post("https://api.openai.com/v1/images/generations", timeout=180,
                          headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                          json={"model": os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1"), "prompt": prompt, "size": size})
        r.raise_for_status()
        return base64.b64decode(r.json()["data"][0]["b64_json"])


class GeminiImage(Provider):
    name = "gemini-image"; kinds = ("image",); env = ("GEMINI_API_KEY",)
    def image(self, prompt, size=None):
        model = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.0-flash-preview-image-generation")
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", timeout=180,
                          headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                          json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}})
        r.raise_for_status()
        for part in r.json()["candidates"][0]["content"]["parts"]:
            if "inlineData" in part:
                return base64.b64decode(part["inlineData"]["data"])
        raise RuntimeError("Gemini returned no image")


class OpenAITTS(Provider):
    name = "openai-tts"; kinds = ("tts",); env = ("OPENAI_API_KEY",)
    def tts(self, text, voice="alloy"):
        r = requests.post("https://api.openai.com/v1/audio/speech", timeout=180,
                          headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                          json={"model": os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"), "voice": voice, "input": text, "format": "mp3"})
        r.raise_for_status()
        return r.content


class VideoManual(Provider):
    name = "video-generation"; kinds = ("video",); env = ("VIDEO_PROVIDER_API_KEY",)
    def available(self): return False  # no video provider integrated; use prompts in video-prompts.md manually


ALL = [OpenAIImage(), GeminiImage(), OpenAITTS(), VideoManual()]


def status():
    return [{"name": p.name, "kinds": list(p.kinds), "env": list(p.env), "available": p.available()} for p in ALL]


def get(kind):
    return next((p for p in ALL if kind in p.kinds and p.available()), None)
