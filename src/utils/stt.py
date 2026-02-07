"""Speech-to-Text utility using Whisper ASR service."""

import httpx
from pathlib import Path
from src.core.config import config
from src.core.logger import logger

class WhisperClient:
    def __init__(self):
        self.base_url = config.whisper_url.rstrip("/")
        
    async def transcribe(self, audio_path: str | Path) -> str:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info(f"🎙️ Transcribing audio: {audio_path.name}")
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                with open(audio_path, "rb") as f:
                    files = {"audio_file": (audio_path.name, f, "audio/ogg")}
                    response = await client.post(
                        f"{self.base_url}/asr",
                        files=files,
                        params={"task": "transcribe", "output": "json"}
                    )
                    response.raise_for_status()
                    data = response.json()
                    transcript = data.get("text", "").strip()
                    logger.info(f"✅ Transcription complete: {transcript[:50]}...")
                    return transcript
        except httpx.HTTPStatusError as e:
            logger.error(f"Whisper API error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to transcribe audio: {e}")
            raise

stt_client = WhisperClient()
