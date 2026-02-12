# Lilith 🌟

<p align="center">Your eccentric genius AI assistant.</p>

<p align="center">
  <img src="lilith.svg" alt="Lilith" width="480">
</p>
<p align="center">
  <sub><em>Glitch effect by <a href="https://metaory.github.io/glitcher-app/">Glitcher App</a></em></sub>
</p>

## Quick Start

```bash
uv sync # or: pip install -e .
cp .env.example .env
python -m src.main cli
```

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/clear` | Clear conversation history |
| `/start` | Start the bot (Telegram) |
| `/external` | Use OpenRouter models |
| `/local` | Use local model |
| `/recover` | Reset agent if stuck after an error |
| `/quit` | Exit Lilith |
| `Ctrl+C` | Interrupt / Exit |

## Testing

```bash
uv sync --extra dev   # install dev deps (pytest, pytest-asyncio)
uv run pytest         # run all tests
uv run pytest tests/unit -v   # unit tests only
uv run pytest tests/e2e -v    # e2e tests only
```

## Prompts

All prompt text lives under `prompts/`. Edit `prompts/soul.md` to customize Lilith's personality.