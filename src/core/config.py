"""Configuration from environment variables (.env)."""

import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    project_root: Path
    data_dir: Path
    logs_dir: Path
    prompts_dir: Path
    soul_file: Path
    vllm_url: str
    vllm_model: str
    searxng_url: str
    flaresolverr_url: str
    crawl4ai_url: str
    telegram_token: str
    telegram_allowed_users: list[int]
    openrouter_api_key: str
    openrouter_models: list[str]  # Model IDs to try in order (fallback on 5xx/429)
    anthropic_api_key: str
    google_client_id: str
    google_client_secret: str
    google_calendar_tokens_path: Path
    google_calendar_default_id: str
    user_timezone: str
    whisper_url: str
    agent_max_iterations: int
    mcp_email_command: str
    mcp_email_args: list[str]
    mcp_email_account_id: int
    mcp_browser_command: str
    mcp_browser_args: list[str]

    @classmethod
    def load(cls) -> "Config":
        project_root = Path(__file__).parent.parent.parent
        return cls(
            project_root=project_root,
            data_dir=project_root / "data",
            logs_dir=project_root / "logs",
            prompts_dir=project_root / "prompts",
            soul_file=project_root / "prompts" / "soul.md",
            vllm_url=os.getenv("VLLM_URL", "http://localhost:6001"),
            vllm_model=os.getenv("VLLM_MODEL", "/models/Meta-Llama-3.1-8B-Instruct-AWQ-INT4"),
            searxng_url=os.getenv("SEARXNG_URL", "http://localhost:6002"),
            flaresolverr_url=os.getenv("FLARESOLVERR_URL", "http://localhost:6003"),
            crawl4ai_url=os.getenv("CRAWL4AI_URL", "http://localhost:6004"),
            telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
            telegram_allowed_users=[int(uid.strip()) for uid in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",") if uid.strip()],
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            openrouter_models=[m.strip() for m in os.getenv("OPENROUTER_MODELS", "openrouter/free").split(",") if m.strip()],
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
            google_calendar_tokens_path=project_root / "data" / "google_calendar_tokens.json",
            google_calendar_default_id=os.getenv("GOOGLE_CALENDAR_DEFAULT_ID", "primary"),
            user_timezone=os.getenv("USER_TIMEZONE", os.getenv("TZ", "UTC")),
            whisper_url=os.getenv("WHISPER_URL", "http://localhost:6002"),
            agent_max_iterations=int(os.getenv("AGENT_MAX_ITERATIONS", "15")),
            mcp_email_command=os.getenv("MCP_EMAIL_COMMAND", ""),
            mcp_email_args=[a.strip() for a in os.getenv("MCP_EMAIL_ARGS", "").split(",") if a.strip()],
            mcp_email_account_id=int(os.getenv("MCP_EMAIL_ACCOUNT_ID", "1")),
            mcp_browser_command=os.getenv("MCP_BROWSER_COMMAND", ""),
            mcp_browser_args=[a.strip() for a in os.getenv("MCP_BROWSER_ARGS", "").split(",") if a.strip()],
        )
    
    def validate(self) -> list[str]:
        errors = []
        if not self.prompts_dir.exists():
            errors.append(f"Prompts directory not found: {self.prompts_dir}")
        elif not self.soul_file.exists():
            errors.append(f"Soul file not found: {self.soul_file}")
        return errors


config = Config.load()
