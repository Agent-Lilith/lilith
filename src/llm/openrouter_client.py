"""OpenRouter client: chat completions using a manual list of model IDs (try in order, fallback on failure)."""

import httpx
import json
from src.core.config import config
from src.core.logger import logger
from src.llm.formatters import ChatMLFormatter


OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class OpenRouterClient:

    def __init__(self, api_key: str = None, models: list[str] = None):
        self.models = models if models is not None else (config.openrouter_models or ["openrouter/free"])
        if not self.models:
            self.models = ["openrouter/free"]
        self.api_key = (api_key or config.openrouter_api_key).strip()
        self.client = httpx.AsyncClient(timeout=120.0)
        self.formatter = ChatMLFormatter()
        self.last_model_used: str | None = None

    def format_prompt(
        self,
        system_message: str,
        conversation: list[dict],
        user_message: str = None
    ) -> str:
        return self.formatter.format(system_message, conversation, user_message)

    def _should_retry(self, e: Exception) -> bool:
        if isinstance(e, httpx.HTTPStatusError):
            return e.response.status_code in (429, 500, 502, 503, 504)
        return True

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop: list[str] = None,
        stream: bool = False
    ):
        messages = [{"role": "user", "content": prompt}]
        payload_base = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if stop:
            payload_base["stop"] = stop
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if stream:
            return self._generate_stream_fallback(payload_base, headers, prompt)
        return await self._generate_non_stream(payload_base, headers, prompt)

    async def _generate_stream_fallback(self, payload_base: dict, headers: dict, prompt: str):
        last_error: Exception | None = None
        for model in self.models:
            logger.llm_request(model=model, is_local=False, prompt_preview=prompt[-200:])
            payload = {**payload_base, "model": model}
            self.last_model_used = None
            try:
                async for chunk in self._generate_stream_one(payload, headers, model):
                    yield chunk
                return
            except Exception as e:
                last_error = e
                if self._should_retry(e):
                    logger.debug(f"OpenRouter {model} failed, trying next: {e}")
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError("No OpenRouter models configured")

    async def _generate_non_stream(self, payload_base: dict, headers: dict, prompt: str):
        last_error: Exception | None = None
        for model in self.models:
            logger.llm_request(model=model, is_local=False, prompt_preview=prompt[-200:])
            payload = {**payload_base, "model": model}
            try:
                response = await self.client.post(
                    f"{OPENROUTER_BASE}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                self.last_model_used = data.get("model") or model
                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})
                generated_text = message.get("content", "") or ""
                tokens_used = data.get("usage", {}).get("total_tokens", 0)
                logger.llm_response(
                    token_count=tokens_used,
                    has_tool_call=False,
                    tool_name=None,
                    page_chars=len(generated_text),
                )
                from src.llm.vllm_client import LLMResponse
                return LLMResponse(
                    text=generated_text,
                    model=self.last_model_used or model,
                    thought="",
                    tokens_used=tokens_used
                )
            except Exception as e:
                last_error = e
                if isinstance(e, httpx.HTTPStatusError):
                    body = getattr(e.response, "text", None) or (e.response.content.decode("utf-8", errors="replace") if e.response.content else "")
                    if body:
                        logger.error(f"OpenRouter {model} {e.response.status_code}: {body[:300]}")
                    else:
                        logger.error(f"OpenRouter {model} {e.response.status_code}")
                else:
                    logger.error(f"OpenRouter {model} failed: {e}")
                if self._should_retry(e):
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError("No OpenRouter models configured")

    async def _generate_stream_one(self, payload: dict, headers: dict, model: str):
        async with self.client.stream(
            "POST",
            f"{OPENROUTER_BASE}/chat/completions",
            json=payload,
            headers=headers,
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", errors="replace")
                logger.error(f"OpenRouter {model} {response.status_code}: {body[:300]}")
                response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                if data_str.startswith(":"):
                    continue
                try:
                    chunk = json.loads(data_str)
                    if self.last_model_used is None and chunk.get("model"):
                        self.last_model_used = chunk["model"]
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content") or ""
                    if text:
                        yield text
                except Exception:
                    continue
            if self.last_model_used is None:
                self.last_model_used = model

    async def close(self):
        await self.client.aclose()
