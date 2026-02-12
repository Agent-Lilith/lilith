"""vLLM client: OpenAI-compatible completions for local inference."""

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from src.core.config import config
from src.core.logger import logger
from src.llm.formatters import get_formatter
from src.observability import trace


@dataclass
class LLMResponse:
    text: str
    model: str
    thought: str = ""
    tokens_used: int = 0


class VLLMClient:
    def __init__(self):
        self.base_url = config.vllm_url
        self.model = config.vllm_model
        self.client = httpx.AsyncClient(timeout=120.0)
        self.formatter = get_formatter(self.model)

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop: list[str] | None = None,
        stream: bool = False,
    ) -> LLMResponse | Any:
        logger.llm_request(
            model=self.model, is_local=True, prompt_preview=prompt[-200:]
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if stop:
            payload["stop"] = stop

        if stream:
            async with trace(
                "vllm_generate",
                "llm",
                inputs={
                    "model": self.model,
                    "prompt_preview": prompt[-500:] if len(prompt) > 500 else prompt,
                    "stream": True,
                },
                metadata={"provider": "vllm"},
            ) as run:
                run.end(outputs={"streamed": True})
            return self._generate_stream(prompt, payload)

        async with trace(
            "vllm_generate",
            "llm",
            inputs={
                "model": self.model,
                "prompt_preview": prompt[-500:] if len(prompt) > 500 else prompt,
            },
            metadata={"provider": "vllm"},
        ) as run:
            try:
                response = await self.client.post(
                    f"{self.base_url}/v1/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                data = response.json()
                generated_text = data["choices"][0]["text"]
                tokens_used = data.get("usage", {}).get("total_tokens", 0)
                thought = ""
                final_text = generated_text
                thought_match = re.search(
                    r"<(thought|think)>(.*?)</\1>", generated_text, re.DOTALL
                )
                if thought_match:
                    thought = thought_match.group(2).strip()
                    final_text = generated_text.replace(
                        thought_match.group(0), ""
                    ).strip()
                elif "Thought:" in generated_text:
                    parts = generated_text.split("Thought:", 1)[1].split("\n\n", 1)
                    if len(parts) > 1:
                        thought = parts[0].strip()
                        final_text = parts[1].strip()
                logger.llm_response(
                    token_count=tokens_used,
                    has_tool_call=False,
                    tool_name=None,
                    page_chars=len(final_text),
                )
                run.end(
                    outputs={
                        "text_preview": final_text[:500],
                        "tokens_used": tokens_used,
                    }
                )
                return LLMResponse(
                    text=final_text,
                    model=self.model,
                    thought=thought,
                    tokens_used=tokens_used,
                )
            except httpx.HTTPStatusError as e:
                body = getattr(e.response, "text", None) or (
                    e.response.content.decode("utf-8", errors="replace")
                    if e.response.content
                    else ""
                )
                if body:
                    logger.error(
                        f"LLM request failed {e.response.status_code}: {body[:500]}"
                    )
                else:
                    logger.error(f"LLM request failed: {e.response.status_code}", e)
                raise
            except Exception as e:
                logger.error("LLM request failed", e)
                raise

    async def _generate_stream(self, prompt: str, payload: dict):
        async with self.client.stream(
            "POST",
            f"{self.base_url}/v1/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", errors="replace")
                logger.error(f"LLM server error {response.status_code}: {body[:500]}")
                response.raise_for_status()
            try:
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        text = chunk["choices"][0]["text"]
                        yield text
                    except Exception:
                        continue
            except (httpx.ReadError, httpx.RemoteProtocolError) as e:
                logger.error(f"LLM streaming connection lost: {e}")
                raise

    def format_prompt(
        self,
        system_message: str,
        conversation: list[dict],
        user_message: str | None = None,
    ) -> str:
        return self.formatter.format(system_message, conversation, user_message)

    async def close(self):
        await self.client.aclose()


def create_client() -> VLLMClient:
    return VLLMClient()
