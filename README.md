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
python -m src.main oneshot "What did I work on last week?"
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
uv sync --extra dev
uv run pytest                  # deterministic suite (default)
uv run pytest tests/unit -v    # unit + property tests
uv run pytest tests/e2e -v --run-integration  # integration/e2e opt-in
```

## Retrieval Eval Gate

```bash
uv run python -m src.eval.benchmark \
  --config benchmarks.yaml \
  --output .artifacts/retrieval_eval/latest.json \
  --report .artifacts/retrieval_eval/latest.md \
  --baseline src/eval/baseline_metrics.json
```

The command writes a metric impact report (`precision@k`, coverage, p95 latency, refinement hit rate)
and fails when thresholds or regression policies are violated.

## Prompts

All prompt text lives under `prompts/`. Edit `prompts/soul.md` to customize Lilith's personality.
