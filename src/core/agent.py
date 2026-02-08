"""Lilith agent: LLM loop with tools and conversation history."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncio
import re

from src.core.bootstrap import save_system_prompt_for_debug, setup_tools
from src.core.config import config
from src.core.logger import logger
from src.core.prompts import fill_date_context
from src.core.tool_call import (
    get_tool_arguments,
    get_tool_name,
    parse_legacy_tool_call,
    parse_tool_call_from_response,
)
from src.core.worker import current_llm_client
from src.llm.vllm_client import VLLMClient, create_client
from src.tools.base import ToolRegistry, ToolResult, parse_pending_confirm


@dataclass
class ChatResult:
    response: str
    pending_confirm: dict | None = None


@dataclass
class Message:
    role: str
    content: str


@dataclass
class Agent:
    llm_client: VLLMClient
    system_prompt: str
    tool_registry: ToolRegistry
    conversation: list[Message] = field(default_factory=list)
    max_history: int = 20
    _pending_confirm: dict | None = field(default=None, repr=False)
    
    @classmethod
    def create(cls) -> "Agent":
        errors = config.validate()
        if errors:
            raise FileNotFoundError("; ".join(errors))
        
        tool_registry = setup_tools()
        system_prompt = save_system_prompt_for_debug(tool_registry)
        llm_client = create_client()

        logger.info("🌟 Lilith Agent initialized!")

        return cls(
            llm_client=llm_client,
            system_prompt=system_prompt,
            tool_registry=tool_registry
        )
    
    async def chat(self, user_input: str, on_event: callable = None, llm_client_override=None) -> ChatResult:
        client = llm_client_override or self.llm_client
        token = current_llm_client.set(client)
        try:
            return await self._chat_impl(user_input, on_event, client)
        finally:
            current_llm_client.reset(token)

    async def _process_stream_chunk(
        self,
        chunk: str,
        response_text: str,
        full_thought: str,
        is_thinking: bool,
        thought_tag_found: bool,
        on_event: callable | None,
    ) -> tuple[str, str, bool, bool]:
        response_text = response_text + chunk

        if not thought_tag_found:
            if "<think>" in response_text or "<thought>" in response_text:
                is_thinking = True
                thought_tag_found = True
            elif "Thought:" in response_text:
                is_thinking = True
                thought_tag_found = True

        if is_thinking:
            match = re.search(r'<(think|thought)>(.*?)$', response_text, re.DOTALL)
            if match:
                raw = match.group(2)
                for close_tag in ("</think>", "</thought>"):
                    if close_tag in raw:
                        raw = raw.split(close_tag)[0]
                new_thought = raw.strip()
                if new_thought != full_thought:
                    full_thought = new_thought
                    if on_event:
                        await on_event("thought", full_thought)
                if "</think>" in response_text or "</thought>" in response_text:
                    is_thinking = False
            elif "Thought:" in response_text:
                parts = response_text.split("Thought:", 1)
                new_thought = parts[1]
                if "\n\n" in new_thought:
                    new_thought = new_thought.split("\n\n")[0]
                    is_thinking = False
                if new_thought != full_thought:
                    full_thought = new_thought
                    if on_event:
                        await on_event("thought", full_thought)

        return response_text, full_thought, is_thinking, thought_tag_found

    def _finalize_stream_response(self, response_text: str) -> tuple[str, str]:
        full_thought = ""
        clean_response = response_text.strip()
        thought_match = re.search(r'<(think|thought)>(.*?)</\1>', response_text, re.DOTALL)
        if thought_match:
            full_thought = thought_match.group(2).strip()
            clean_response = response_text.replace(thought_match.group(0), "").strip()
        elif "Thought:" in response_text:
            parts = response_text.split("Thought:", 1)[1].split("\n\n", 1)
            full_thought = parts[0].strip()
            clean_response = parts[1].strip() if len(parts) > 1 else ""
        return full_thought, clean_response

    async def _chat_impl(self, user_input: str, on_event: callable, client) -> ChatResult:
        if user_input:
            logger.user_input(user_input)
            self.conversation.append(Message(role="user", content=user_input))
            if on_event:
                await on_event("user_input", user_input)
        
        self._trim_history()
        
        max_iterations = config.agent_max_iterations
        for iteration in range(max_iterations):
            prompt = self._build_prompt(client)
            logger.context_built(
                token_count=len(prompt) // 4,
                message_count=len(self.conversation)
            )
            response_text = ""
            full_thought = ""
            is_thinking = False
            thought_tag_found = False
            
            generator = await client.generate(
                prompt=prompt,
                max_tokens=2048,
                temperature=0.4,
                stop=client.formatter.stop_tokens,
                stream=True
            )
            
            async for chunk in generator:
                response_text, full_thought, is_thinking, thought_tag_found = await self._process_stream_chunk(
                    chunk, response_text, full_thought, is_thinking, thought_tag_found, on_event
                )

            logger.llm_stream_done()
            full_thought, clean_response = self._finalize_stream_response(response_text)
            if full_thought:
                logger.thought(full_thought)

            valid_tool_names = {t.name for t in self.tool_registry.list_tools()}
            parsed, json_end = parse_tool_call_from_response(clean_response, valid_tool_names)
            text_for_slice = clean_response
            if parsed is None and response_text.strip() != clean_response:
                parsed, json_end = parse_tool_call_from_response(response_text.strip(), valid_tool_names)
                if parsed is not None and json_end >= 0:
                    text_for_slice = response_text.strip()

            if parsed is not None and json_end >= 0:
                tool_name = get_tool_name(parsed)
                args = get_tool_arguments(parsed)
                assistant_content = text_for_slice[:json_end].strip()
                if on_event:
                    await on_event("replace_response", assistant_content)
                await self._execute_tool_turn(tool_name, args, assistant_content, on_event)
                continue

            legacy = parse_legacy_tool_call(clean_response, valid_tool_names)
            if legacy is not None:
                tool_name, args, assistant_content = legacy
                await self._execute_tool_turn(tool_name, args, assistant_content, on_event)
                continue
            else:
                self.conversation.append(Message(role="assistant", content=clean_response))
                logger.final_response(clean_response)
                if on_event:
                    await on_event("final_response", clean_response)
                out = ChatResult(response=clean_response, pending_confirm=self._pending_confirm)
                self._pending_confirm = None
                return out
        
        msg = "I've reached my step limit for this request. The tool results above are what I gathered; ask me to summarize them if you want a combined answer."
        if on_event:
            await on_event("final_response", msg)
        return ChatResult(response=msg, pending_confirm=None)
    
    def _build_prompt(self, llm_client=None) -> str:
        if llm_client is None:
            llm_client = self.llm_client
        tz_name = config.user_timezone or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc
            tz_name = "UTC"
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(tz)
        date_line = (
            f"\n\nUser's timezone: {tz_name}. Current local time: {now_local.strftime('%Y-%m-%d')} {now_local.strftime('%H:%M')}. "
            "When the user says '5PM' or 'today at 5pm', use that hour in the user's timezone. "
            "For calendar_write create, pass start/end as local time WITHOUT Z (e.g. start=\"2026-02-04T17:00:00\", end=\"2026-02-04T18:00:00\"). Do NOT use UTC/Z unless the user explicitly says UTC."
        )
        system_message = fill_date_context(self.system_prompt, date_line)
        history = [
            {"role": msg.role, "content": msg.content}
            for msg in self.conversation[:-1]
        ]
        current_input = self.conversation[-1].content if self.conversation else ""

        return llm_client.format_prompt(
            system_message=system_message,
            conversation=history,
            user_message=current_input
        )
    
    def _trim_history(self):
        if len(self.conversation) > self.max_history:
            self.conversation = self.conversation[-(self.max_history):]
            logger.debug(f"Trimmed history to {len(self.conversation)} messages")
    
    def clear_history(self):
        self.conversation = []
        logger.info("🧹 Conversation cleared")

    async def _execute_tool_turn(
        self,
        tool_name: str,
        args: dict,
        assistant_content: str,
        on_event: callable,
    ) -> None:
        self.conversation.append(Message(role="assistant", content=assistant_content))
        if on_event:
            await on_event("tool_call", {"name": tool_name, "args": args})
        tool = self.tool_registry.get(tool_name)
        if not tool:
            result = ToolResult.fail(f"Tool '{tool_name}' not found.")
        else:
            try:
                result = await tool.execute(**args)
            except Exception as e:
                result = ToolResult.fail(str(e))
        result_content = result.output if result.success else result.error
        result_msg = f"TOOL_RESULT({tool_name}): {result_content}"
        self.conversation.append(Message(role="user", content=result_msg))
        if on_event:
            await on_event("tool_result", {"name": tool_name, "result": result_content, "success": result.success})
        self._pending_confirm = parse_pending_confirm(result_content) if result.success else None
    
    async def close(self):
        await self.llm_client.close()
        for tool in self.tool_registry.list_tools():
            if hasattr(tool, "close"):
                if asyncio.iscoroutinefunction(tool.close):
                    await tool.close()
                else:
                    tool.close()
        logger.info("👋 Lilith shutting down")
