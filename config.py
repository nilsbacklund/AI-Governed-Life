from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
import os


@dataclass
class Config:
    telegram_bot_token: str
    telegram_chat_id: int
    tavily_api_key: str
    timezone: str
    default_location: str
    default_lat: float
    default_lon: float
    model: str
    max_tokens: int
    token_threshold: int
    data_dir: Path
    logs_dir: Path
    history_file: Path


def _require(name: str) -> str:
    """Return env var value or raise ValueError if missing/empty."""
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ValueError(f"Required environment variable {name} is missing or empty")
    return value


def load_config() -> Config:
    load_dotenv()
    base = Path(__file__).parent
    return Config(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=int(_require("TELEGRAM_CHAT_ID")),
        tavily_api_key=_require("TAVILY_API_KEY"),
        timezone=os.getenv("TIMEZONE", "Europe/Amsterdam"),
        default_location=os.getenv("DEFAULT_LOCATION", "Eindhoven"),
        default_lat=float(os.getenv("DEFAULT_LAT", "51.4416")),
        default_lon=float(os.getenv("DEFAULT_LON", "5.4697")),
        model=os.getenv("MODEL", "vertex_ai/zai-org/glm-5-maas"),
        max_tokens=int(os.getenv("MAX_TOKENS", "4096")),
        token_threshold=int(os.getenv("TOKEN_THRESHOLD", "80000")),
        data_dir=base / "data",
        logs_dir=base / "logs",
        history_file=base / "history.json",
    )
