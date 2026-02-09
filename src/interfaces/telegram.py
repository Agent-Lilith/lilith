"""Telegram interface: bot loop, commands, and AI chat."""

import asyncio
import logging
import html
import re
import bleach
from markdown_it import MarkdownIt
from telegram import BotCommand, Update, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

from src.core.agent import Agent, ChatResult

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
from src.core.config import config
from src.core.logger import logger
from src.llm.openrouter_client import OpenRouterClient
from src.utils.confirm import get_confirmation_result
from src.utils.stt import stt_client

user_agents: dict[int, Agent] = {}
user_model_mode: dict[int, str] = {}
user_last_run_log: dict[int, dict] = {}

class ThrottledEditor:
    def __init__(self, message, initial_content="", parse_mode=ParseMode.HTML, use_markdown=False):
        self.message = message
        self.content = initial_content
        self.parse_mode = parse_mode
        self.use_markdown = use_markdown
        self.last_edit_time = 0
        self.edit_task = None
        self.pending_edit = False
        self.lock = asyncio.Lock()

    async def update(self, new_content, force=False):
        if not new_content or not new_content.strip():
            return
            
        async with self.lock:
            self.content = new_content
            if force:
                await self._do_edit()
                return

            if self.edit_task is None or self.edit_task.done():
                now = asyncio.get_event_loop().time()
                wait_time = max(0, 1.1 - (now - self.last_edit_time))
                if wait_time <= 0:
                    await self._do_edit()
                else:
                    self.edit_task = asyncio.create_task(self._delayed_edit(wait_time))

    async def _delayed_edit(self, delay):
        await asyncio.sleep(delay)
        async with self.lock:
            await self._do_edit()

    async def _do_edit(self):
        try:
            content = self.content
            if self.use_markdown:
                content = md_to_html(content)
            safe_content = clean_html(content)
            
            if not safe_content.strip():
                return
                
            await self.message.edit_text(
                safe_content,
                parse_mode=self.parse_mode,
                link_preview_options=NO_PREVIEW,
            )
            self.last_edit_time = asyncio.get_event_loop().time()
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"ThrottledEditor error: {e}")

_TELEGRAM_HTML_TAGS = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-spoiler"}
_TELEGRAM_HTML_ATTRS = {"a": ["href", "title"]}

_md_renderer = MarkdownIt("commonmark", {"breaks": True})


def md_to_html(text: str) -> str:
    if not text or not text.strip():
        return ""
    html_out = _md_renderer.render(text)
    html_out = html_out.replace("<strong>", "<b>").replace("</strong>", "</b>")
    html_out = html_out.replace("<em>", "<i>").replace("</em>", "</i>")
    html_out = html_out.replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n")
    return html_out


def clean_html(text: str) -> str:
    if not text or not text.strip():
        return ""
    return bleach.clean(
        text,
        tags=_TELEGRAM_HTML_TAGS,
        attributes=_TELEGRAM_HTML_ATTRS,
        strip=True,
    )

def split_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        
        split_at = text.rfind('\n', 0, limit)
        if split_at == -1:
            split_at = limit
        
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    return chunks

def get_agent(user_id: int) -> Agent:
    if user_id not in user_agents:
        user_agents[user_id] = Agent.create()
    return user_agents[user_id]


async def _reset_agent_for_user(user_id: int) -> None:
    if user_id not in user_agents:
        return
    agent = user_agents.pop(user_id)
    try:
        await agent.close()
    except Exception as e:
        logger.warning(f"Error closing agent during reset: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("I'm sorry, Dave. I'm afraid I can't do that. (Unauthorized)")
        return

    welcome_text = (
        f"Hi {user.first_name}! 🌟 I'm Lilith, your eccentric genius assistant.\n\n"
        "I'm here to help you with research, organization, and whatever else my brilliant brain can handle.\n\n"
        "Try talking to me or use /help to see what I can do!"
    )
    await update.message.reply_text(welcome_text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "<b>Commands:</b>\n"
        "/start - Start the bot\n"
        "/help - Show this help\n"
        "/clear - Clear conversation history\n"
        "/recover - Reset agent if stuck after an error\n"
        "/external - Use OpenRouter free models\n"
        "/local - Use local model\n\n"
        "Just talk to me! I'll stream my brilliant thoughts in real-time. ✨"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_agents:
        user_agents[user_id].clear_history()
        await update.message.reply_text("🧹 Conversation cleared! ✨")
    else:
        await update.message.reply_text("Nothing to clear yet~")


async def recover_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    user_id = update.effective_user.id
    await _reset_agent_for_user(user_id)
    msg = (
        "🔄 <b>Recovery complete</b>\n\n"
        "All conversation history has been cleared. You can send a new message."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


async def external_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not config.openrouter_api_key or not config.openrouter_api_key.strip():
        await update.message.reply_text(
            "OpenRouter is not configured. Set OPENROUTER_API_KEY in .env to use external models."
        )
        return
    user_model_mode[update.effective_user.id] = "external"
    await update.message.reply_text(
        "Using OpenRouter free models. Send /local to switch back to your local model."
    )


async def local_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    user_model_mode[update.effective_user.id] = "local"
    await update.message.reply_text("Using local model.")


def is_authorized(user_id: int) -> bool:
    if not config.telegram_allowed_users:
        return True
    return user_id in config.telegram_allowed_users


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    user_text = update.message.text
    if not user_text:
        return

    agent = get_agent(user_id)
    use_external = user_model_mode.get(user_id, "local") == "external"
    openrouter_client = OpenRouterClient() if use_external else None

    response_msg = await update.message.reply_text(
        "<i>...</i>",
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
    response_editor = ThrottledEditor(response_msg, "<i>...</i>", use_markdown=True)
    await _process_agent_chat(
        update, context, user_text,
        status_editor=None,
        activities=None,
        response_msg=response_msg,
        response_editor=response_editor,
        llm_client_override=openrouter_client,
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    voice = update.message.voice
    if not voice:
        return

    status_msg = await update.message.reply_text(
        "Downloading...",
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )
    status_editor = ThrottledEditor(status_msg, "Downloading...")

    try:
        new_file = await context.bot.get_file(voice.file_id)
        voice_dir = config.data_dir / "voice"
        voice_dir.mkdir(parents=True, exist_ok=True)
        voice_file_path = voice_dir / f"voice_{voice.file_unique_id}.ogg"
        await new_file.download_to_drive(custom_path=voice_file_path)

        await status_editor.update("Transcribing...", force=True)
        transcribed_text = await stt_client.transcribe(voice_file_path)
        agent_input = f"🎤 (Voice): {transcribed_text}"

        transcribed_display = f"<i>{html.escape(transcribed_text)}</i>"
        await status_editor.update(transcribed_display, force=True)

        use_external = user_model_mode.get(user_id, "local") == "external"
        openrouter_client = OpenRouterClient() if use_external else None
        await _process_agent_chat(
            update, context, agent_input,
            status_editor=None,
            activities=None,
            response_msg=None,
            response_editor=None,
            llm_client_override=openrouter_client,
        )

    except Exception as e:
        logger.error(f"Error handling voice message: {e}", exc_info=True)
        await status_editor.update(f"❌ Error: {html.escape(str(e))}", force=True)
    finally:
        if "voice_file_path" in locals() and voice_file_path.exists():
            voice_file_path.unlink()

def _format_run_log(model: str, thought: str, activities: list[str]) -> str:
    parts = []
    if model:
        parts.append(f"Model\n{model}")
    if thought and thought.strip():
        parts.append(f"Thinking\n{thought.strip()}")
    if activities:
        parts.append("Tools")
        for line in activities:
            plain = re.sub(r"<[^>]+>", "", line).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            parts.append(f"  {plain}")
    if not parts:
        return "No details for this reply."
    return "\n\n".join(parts)


async def _process_agent_chat(
    update, context, user_text,
    status_editor=None,
    activities=None,
    response_msg=None,
    response_editor=None,
    llm_client_override=None,
):
    user_id = update.effective_user.id
    agent = get_agent(user_id)
    model_name = config.vllm_model

    if response_msg is None:
        response_msg = await update.message.reply_text(
            "<i>...</i>",
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        response_editor = ThrottledEditor(response_msg, "<i>...</i>", use_markdown=True)

    has_status_hub = status_editor is not None and activities is not None
    if has_status_hub:
        activities.append(f"<code>{model_name}</code>")
    run_log: list[str] = []
    thought_buffer = ""
    response_buffer = ""

    async def update_hub():
        if has_status_hub:
            content = "\n".join(activities)
            await status_editor.update(f"<i>{content}</i>")

    async def on_event(event_type, data):
        nonlocal response_msg, response_editor, response_buffer, thought_buffer
        try:
            if event_type == "thought":
                thought_buffer = data
            elif event_type == "replace_response":
                response_buffer = data if isinstance(data, str) else ""
            elif event_type == "token":
                response_buffer += data
                if response_msg is None:
                    await update_hub()
                    response_msg = await update.message.reply_text(
                        "<i>...</i>",
                        parse_mode=ParseMode.HTML,
                        link_preview_options=NO_PREVIEW,
                    )
                    response_editor = ThrottledEditor(response_msg, use_markdown=True)
                await response_editor.update(response_buffer)
            elif event_type == "tool_call":
                tool_name = data["name"]
                args = data.get("args", {})
                args_str = ", ".join([f"{k}={repr(v)}" for k, v in args.items()])
                entry = f"<code>{tool_name}</code>({html.escape(args_str)})"
                run_log.append(f"• {entry}")
                if has_status_hub:
                    activities.append(f"<code>{tool_name}({args_str})</code>")
                    await update_hub()
            elif event_type == "tool_result":
                tool_name = data["name"]
                result_raw = data.get("result") or ""
                max_preview = 350
                result_preview = result_raw[:max_preview].strip()
                if len(result_raw) > max_preview:
                    result_preview += " … (truncated)"
                run_log.append(f"  → <b>{html.escape(tool_name)}</b> done" + (f" — {html.escape(result_preview)}" if result_preview else ""))
                if has_status_hub:
                    activities[-1] = f"<b>{tool_name}</b> complete"
                    await update_hub()
        except Exception as e:
            logger.error(f"Error in on_event: {e}")

    try:
        result = await agent.chat(user_text, on_event=on_event, llm_client_override=llm_client_override)
        response = result.response if isinstance(result, ChatResult) else result

        if llm_client_override:
            models = getattr(llm_client_override, "models", None)
            model_name = getattr(llm_client_override, "last_model_used", None) or (models[0] if models else "openrouter")

        if has_status_hub:
            final_hub = "\n".join(activities)
            if thought_buffer:
                final_hub += f"\n\n<blockquote expandable>{thought_buffer}</blockquote>"
            await status_editor.update(final_hub, force=True)

        final_response = response
        reply_markup = None
        
        if isinstance(result, ChatResult) and result.pending_confirm:
            pending = result.pending_confirm
            summary = pending.get("summary", "Proceed?")
            pending_id = pending.get("pending_id", "")
            tool_name = pending.get("tool", "calendar_write")
            
            keyboard = [[
                InlineKeyboardButton("Yes ✅", callback_data=f"confirm:{tool_name}:{pending_id}"),
                InlineKeyboardButton("No ❌", callback_data=f"cancel:{tool_name}:{pending_id}"),
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            final_response += f"\n\n<b>{summary}</b>"

        user_last_run_log[user_id] = {"model": model_name, "thought": thought_buffer, "activities": run_log}
        has_details = True
        if has_details:
            if reply_markup is None:
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Details", callback_data="details")]])
            else:
                rows = list(reply_markup.inline_keyboard) + [[InlineKeyboardButton("Details", callback_data="details")]]
                reply_markup = InlineKeyboardMarkup(rows)

        if response_msg is None:
            response_chunks = split_message(final_response)
            response_msg = await update.message.reply_text(
                response_chunks[0],
                reply_markup=reply_markup if len(response_chunks) == 1 else None,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            for i in range(1, len(response_chunks)):
                markup = reply_markup if i == len(response_chunks) - 1 else None
                await update.message.reply_text(
                    response_chunks[i],
                    reply_markup=markup,
                    parse_mode=ParseMode.HTML,
                    link_preview_options=NO_PREVIEW,
                )
        else:
            chunks = split_message(final_response)
            await response_editor.update(chunks[0], force=True)
            if reply_markup and len(chunks) == 1:
                await response_msg.edit_reply_markup(reply_markup)
            
            for i in range(1, len(chunks)):
                markup = reply_markup if i == len(chunks) - 1 else None
                await update.message.reply_text(
                    chunks[i],
                    reply_markup=markup,
                    parse_mode=ParseMode.HTML,
                    link_preview_options=NO_PREVIEW,
                )

    except Exception as e:
        logger.error(f"Error in processing: {e}", exc_info=True)
        await _reset_agent_for_user(user_id)
        err_text = (
            f"Oops! My genius brain had a hiccup: {e}\n\n"
            "🔄 <b>Recovery complete</b> — all history cleared. Please try again."
        )
        if response_msg:
            await response_msg.edit_text(
                err_text,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
        else:
            await update.message.reply_text(
                err_text,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    agent = get_agent(user_id)
    
    if data.startswith("confirm:"):
        _, tool_name, pending_id = data.split(":", 2)
        pending = {"tool": tool_name, "pending_id": pending_id, "summary": ""}
        loop = asyncio.get_event_loop()
        message, success = await loop.run_in_executor(
            None,
            lambda: get_confirmation_result(agent.tool_registry, pending, True),
        )
        status = "✅" if success else "❌"
        await query.edit_message_text(text=f"{query.message.text}\n\n{status} {message}")

    elif data.startswith("cancel:"):
        await query.edit_message_text(text=f"{query.message.text}\n\n🚫 Cancelled.")

    elif data == "details":
        if not is_authorized(user_id):
            return
        log = user_last_run_log.get(user_id)
        if not log:
            await query.message.reply_text(
                "<i>No run log available for the last reply.</i>",
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
            return
        body = _format_run_log(
            log.get("model") or "",
            log.get("thought") or "",
            log.get("activities") or [],
        )
        for chunk in split_message(body, limit=4000):
            await query.message.reply_text(
                chunk,
                link_preview_options=NO_PREVIEW,
            )


async def _telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if err is None:
        return
    msg = str(err).strip() or err.__class__.__name__
    if isinstance(err, NetworkError) or "disconnected" in msg.lower() or "connection" in msg.lower():
        logger.warning(f"Telegram network: {msg}")
    else:
        logger.error(f"Telegram: {msg}", exception=err)


def _get_handlers() -> list:
    return [
        CommandHandler("start", start),
        CommandHandler("help", help_command),
        CommandHandler("clear", clear_command),
        CommandHandler("recover", recover_command),
        CommandHandler("external", external_command),
        CommandHandler("local", local_command),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
        MessageHandler(filters.VOICE, handle_voice),
        CallbackQueryHandler(handle_callback),
    ]


async def post_init(application: Application):
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Show help info"),
        BotCommand("clear", "Clear conversation history"),
        BotCommand("recover", "Reset agent if stuck"),
        BotCommand("external", "Use OpenRouter models"),
        BotCommand("local", "Use local model"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("✅ Telegram bot commands registered.")


def run_telegram():
    if not config.telegram_token:
        logger.error("TELEGRAM_TOKEN not set!")
        print("Error: TELEGRAM_TOKEN not set in .env")
        return

    logger.info("🚀 Starting Lilith Telegram Bot...")
    application = (
        Application.builder()
        .token(config.telegram_token)
        .connect_timeout(20.0)
        .read_timeout(20.0)
        .write_timeout(20.0)
        .get_updates_connect_timeout(20.0)
        .get_updates_read_timeout(20.0)
        .post_init(post_init)
        .build()
    )

    for handler in _get_handlers():
        application.add_handler(handler)
    application.add_error_handler(_telegram_error_handler)

    application.run_polling()


if __name__ == "__main__":
    run_telegram()
