"""CLI interface: chat loop, colors, /help /clear /quit, calendar confirmation."""

import sys
import readline
import textwrap
import shutil
import asyncio

from src.core.agent import Agent, ChatResult
from src.core.config import config
from src.core.logger import logger
from src.llm.openrouter_client import OpenRouterClient
from src.observability import flush, trace
from src.utils.confirm import run_confirmation_flow


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Foreground
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    
    # Background
    BG_BLUE = "\033[44m"


def colorize(text: str, *colors: str) -> str:
    color_codes = "".join(colors)
    return f"{color_codes}{text}{Colors.RESET}"


def print_banner():
    banner = """
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║   ██╗     ██╗██╗     ██╗████████╗██╗  ██╗                ║
    ║   ██║     ██║██║     ██║╚══██╔══╝██║  ██║                ║
    ║   ██║     ██║██║     ██║   ██║   ███████║                ║
    ║   ██║     ██║██║     ██║   ██║   ██╔══██║                ║
    ║   ███████╗██║███████╗██║   ██║   ██║  ██║                ║
    ║   ╚══════╝╚═╝╚══════╝╚═╝   ╚═╝   ╚═╝  ╚═╝                ║
    ║                                                           ║
    ║   Your eccentric genius AI assistant                      ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """
    print(colorize(banner, Colors.MAGENTA))


def print_help(use_external: bool = False):
    help_text = """
    ╭─────────────────────────────────────╮
    │  Commands                           │
    ├─────────────────────────────────────┤
    │  /help     - Show this help         │
    │  /clear    - Clear conversation     │
    │  /recover  - Reset agent if stuck   │
    │  /external - Use OpenRouter models  │
    │  /local    - Use local model        │
    │  /quit     - Exit Lilith            │
    │  Ctrl+C    - Interrupt / Exit       │
    ╰─────────────────────────────────────╯
    To connect Google Calendar and Tasks, run once: python -m src.main google-auth
    """
    if use_external:
        help_text += "\n    Mode: OpenRouter free models\n"
    print(colorize(help_text, Colors.CYAN))


def format_response(text: str) -> str:
    prefix = colorize("┃ ", Colors.MAGENTA)
    try:
        terminal_width = shutil.get_terminal_size().columns
    except OSError:
        terminal_width = 80
        
    wrap_width = max(terminal_width - 4, 40)
    lines = text.split("\n")
    formatted_lines = []
    
    for line in lines:
        if not line.strip():
            formatted_lines.append(prefix)
            continue
        wrapped = textwrap.wrap(
            line, 
            width=wrap_width, 
            replace_whitespace=False, 
            drop_whitespace=False
        )
        for i, w_line in enumerate(wrapped):
            formatted_lines.append(f"{prefix}{w_line}")
            
    return "\n".join(formatted_lines)


async def run_cli(initial_external: bool = False):
    if initial_external:
        if not config.openrouter_api_key or not config.openrouter_api_key.strip():
            print(colorize("  Error: OPENROUTER_API_KEY is not set. Set it in .env to use --external.", Colors.RED))
            return

    use_external = initial_external
    openrouter_client: OpenRouterClient | None = None

    def get_llm_client():
        if use_external:
            if not openrouter_client:
                return OpenRouterClient()
            return openrouter_client
        return None

    if use_external:
        openrouter_client = OpenRouterClient()
        print(colorize("  Mode: OpenRouter free models (--external)\n", Colors.DIM))

    print_banner()
    print(colorize("  Type /help for commands\n", Colors.DIM))
    agent = Agent.create()
    try:
        while True:
            try:
                user_input = input(colorize("\n❯ ", Colors.GREEN, Colors.BOLD))
            except EOFError:
                break
            if not user_input.strip():
                continue
            command = user_input.strip().lower()
            
            if command == "/help":
                print_help(use_external=use_external)
                continue
            
            if command == "/clear":
                agent.clear_history()
                print(colorize("  Conversation cleared ✨\n", Colors.YELLOW))
                continue

            if command == "/recover":
                await agent.close()
                agent = Agent.create()
                print(colorize("  Recovery complete", Colors.GREEN, Colors.BOLD))
                print(colorize("  All conversation history has been cleared.", Colors.DIM))
                print(colorize("  You can send a new message.\n", Colors.DIM))
                continue

            if command == "/external":
                if not config.openrouter_api_key or not config.openrouter_api_key.strip():
                    print(colorize("  Error: OPENROUTER_API_KEY is not set in .env.", Colors.RED))
                    continue
                use_external = True
                if openrouter_client is None:
                    openrouter_client = OpenRouterClient()
                print(colorize("  Switched to OpenRouter models.\n", Colors.YELLOW))
                continue

            if command == "/local":
                use_external = False
                print(colorize("  Switched to local model.\n", Colors.YELLOW))
                continue
            
            if command in ("/quit", "/exit", "/q"):
                print(colorize("\n  Goodbye! See you soon~ ✨\n", Colors.MAGENTA))
                break
            try:
                print()

                async def on_event(event_type, data):
                    if event_type == "token":
                        sys.stdout.write(colorize(data, Colors.MAGENTA))
                        sys.stdout.flush()
                    elif event_type == "thought":
                        pass

                llm_client = get_llm_client() if use_external else None
                async with trace(
                    "Lilith Chat",
                    "chain",
                    inputs={"user_message": user_input[:500]},
                    metadata={"interface": "cli"},
                ) as run:
                    result = await agent.chat(user_input, on_event=on_event, llm_client_override=llm_client)
                    response = result.response if isinstance(result, ChatResult) else result
                    run.end(outputs={"response_preview": (response or "")[:500]})
                response = result.response if isinstance(result, ChatResult) else result
                sys.stdout.write("\r" + " " * shutil.get_terminal_size().columns + "\r")
                print(format_response(response))
                if isinstance(result, ChatResult) and result.pending_confirm:
                    pending = result.pending_confirm
                    if not pending.get("pending_id"):
                        continue

                    async def prompt_user(summary: str) -> bool:
                        try:
                            reply = await asyncio.to_thread(
                                lambda: input(colorize(f"  {summary} [y/N] ", Colors.YELLOW)).strip().lower()
                            )
                        except EOFError:
                            reply = "n"
                        return reply in ("y", "yes")

                    async def on_result(msg: str, success: bool | None) -> None:
                        if success is True:
                            c = Colors.GREEN
                        elif success is False:
                            c = Colors.RED
                        else:
                            c = Colors.DIM
                        print(format_response(colorize(msg, c)))

                    await run_confirmation_flow(
                        agent.tool_registry,
                        pending,
                        prompt_user=prompt_user,
                        on_result=on_result,
                    )
            except KeyboardInterrupt:
                print(colorize("\n  [Interrupted]", Colors.YELLOW))
                continue
            except Exception as e:
                logger.error(f"Error during chat: {e}", exc_info=True)
                await agent.close()
                agent = Agent.create()
                print(colorize(f"\n  Error: {e}", Colors.RED))
                print(colorize("  Recovery complete — all history cleared. Please try again.\n", Colors.YELLOW))
                continue
    
    except KeyboardInterrupt:
        print(colorize("\n\n  Goodbye! See you soon~ ✨\n", Colors.MAGENTA))
    finally:
        await agent.close()
        if openrouter_client:
            await openrouter_client.close()
        flush()


def main():
    try:
        asyncio.run(run_cli())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
