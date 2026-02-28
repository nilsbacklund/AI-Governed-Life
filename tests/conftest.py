import pytest
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from config import Config
from plugins import PluginRegistry
from timer import WakeupTimer
from tools import init_tools


@pytest.fixture
def tz():
    return ZoneInfo("Europe/Amsterdam")


@pytest.fixture
def setup_tools(tmp_path, tz):
    """Initialize tools with fake config pointing at tmp_path, AsyncMock send_fn, real timer."""
    send_fn = AsyncMock()
    config = Config(
        gcp_project_id="GeneralOrder",
        gcp_region="europe-west1",
        telegram_bot_token="test-bot-token",
        telegram_chat_id=12345,
        tavily_api_key="test-tavily-key",
        timezone="Europe/Amsterdam",
        default_location="Eindhoven",
        default_lat=51.4416,
        default_lon=5.4697,
        model="claude-sonnet-4-6-20250514",
        max_tokens=4096,
        token_threshold=80000,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        history_file=tmp_path / "history.json",
    )
    (tmp_path / "data").mkdir()
    timer = WakeupTimer(tz)
    registry = PluginRegistry()
    init_tools(send_fn, config, timer, registry)
    return send_fn, config, timer
