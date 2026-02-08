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
| `/external` | Use OpenRouter models (Telegram) |
| `/local` | Use local model (Telegram) |
| `/recover` | Reset agent if stuck after an error (Telegram) |
| `/quit` | Exit Lilith |
| `Ctrl+C` | Interrupt / Exit |

## Prompts

All prompt text lives under `prompts/`. Edit `prompts/soul.md` to customize Lilith's personality.