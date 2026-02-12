"""Prompt formatters for Llama3 and ChatML-style models."""

from abc import ABC, abstractmethod


class BaseFormatter(ABC):
    @abstractmethod
    def format(
        self,
        system_message: str,
        conversation: list[dict[str, str]],
        user_message: str | None = None,
    ) -> str:
        pass

    @property
    @abstractmethod
    def stop_tokens(self) -> list[str]:
        pass


class Llama3Formatter(BaseFormatter):
    def format(
        self,
        system_message: str,
        conversation: list[dict[str, str]],
        user_message: str | None = None,
    ) -> str:
        prompt = f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n{system_message}<|eot_id|>"

        for msg in conversation:
            role = msg["role"]
            content = msg["content"]
            prompt += (
                f"<|start_header_id|>{role}<|end_header_id|>\n\n{content}<|eot_id|>"
            )

        if user_message:
            prompt += (
                f"<|start_header_id|>user<|end_header_id|>\n\n{user_message}<|eot_id|>"
            )

        prompt += "<|start_header_id|>assistant<|end_header_id|>\n\n"
        return prompt

    @property
    def stop_tokens(self) -> list[str]:
        return ["<|eot_id|>", "<|end_of_text|>"]


class ChatMLFormatter(BaseFormatter):
    def format(
        self,
        system_message: str,
        conversation: list[dict[str, str]],
        user_message: str | None = None,
    ) -> str:
        prompt = f"<|im_start|>system\n{system_message}<|im_end|>\n"
        for msg in conversation:
            role = msg["role"]
            content = msg["content"]
            prompt += f"<|im_start|>{role}\n{content}<|im_end|>\n"

        if user_message:
            prompt += f"<|im_start|>user\n{user_message}<|im_end|>\n"

        prompt += "<|im_start|>assistant\n"
        return prompt

    @property
    def stop_tokens(self) -> list[str]:
        return ["<|im_end|>", "<|endoftext|>"]


def get_formatter(model_name: str) -> BaseFormatter:
    model_lower = model_name.lower()
    if "llama-3" in model_lower:
        return Llama3Formatter()
    if "qwen" in model_lower or "chatml" in model_lower:
        return ChatMLFormatter()
    return Llama3Formatter()
