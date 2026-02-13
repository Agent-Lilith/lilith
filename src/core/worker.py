"""Out-of-context LLM worker for summarization and extraction."""

import contextvars
from typing import Any

from src.core.logger import logger
from src.core.prompts import load_worker_prompt
from src.llm.vllm_client import VLLMClient, create_client

current_llm_client: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "current_llm_client", default=None
)


def _strip_conversation_tokens(text: str) -> str:
    """Remove ChatML/Llama template tokens from worker output."""
    if not text or not text.strip():
        return text
    s = text.strip()
    for _ in range(6):
        s_prev = s
        if s.startswith("<|start_header_id|>"):
            idx = s.find("<|end_header_id|>")
            if idx != -1:
                s = s[idx + len("<|end_header_id|>") :].lstrip()
        if s.startswith("<|im_start|>"):
            idx = s.find("\n")
            s = (s[idx + 1 :] if idx != -1 else "").lstrip()
        if s.startswith("<|im_end|>"):
            s = s[len("<|im_end|>") :].lstrip()
        if s.startswith("<|eot_id|>"):
            s = s[len("<|eot_id|>") :].lstrip()
        if s == s_prev:
            break
    for _ in range(3):
        if s.endswith("<|im_end|>"):
            s = s[: -len("<|im_end|>")].rstrip()
        elif s.endswith("<|eot_id|>"):
            s = s[: -len("<|eot_id|>")].rstrip()
        else:
            break
    return s.strip()


class Worker:
    def __init__(self, client: VLLMClient | None = None):
        self.client = client or create_client()
        self._prompt_template: str | None = None

    def _get_template(self) -> str:
        if self._prompt_template is None:
            self._prompt_template = load_worker_prompt()
        return self._prompt_template

    async def process(
        self,
        task_description: str,
        data: str,
        instruction: str = "Format the result as a concise summary with bullet points.",
    ) -> str:
        template = self._get_template()
        system_message = (
            template.replace("{task_description}", task_description)
            .replace("{instruction}", instruction)
        )
        user_message = f"DATA TO PROCESS:\n---\n{data}\n---\n\nAction: {task_description}"

        logger.debug(f"Worker task started: {task_description[:50]}...")
        client = current_llm_client.get() or self.client
        prompt = client.format_prompt(
            system_message=system_message,
            conversation=[],
            user_message=user_message,
        )
        stop = getattr(getattr(client, "formatter", None), "stop_tokens", None) or [
            "<|eot_id|>",
            "<|end_of_text|>",
        ]
        response = await client.generate(
            prompt=prompt,
            max_tokens=1024,
            temperature=0.2,
            stop=stop,
        )
        result = (response.text if hasattr(response, "text") else str(response)).strip()
        result = _strip_conversation_tokens(result)
        logger.debug(
            f"Worker task completed. Input: {len(data)} chars -> Output: {len(result)} chars"
        )
        return result

    async def close(self):
        await self.client.close()


_global_worker = None


def get_worker() -> Worker:
    global _global_worker
    if _global_worker is None:
        _global_worker = Worker()
    return _global_worker
