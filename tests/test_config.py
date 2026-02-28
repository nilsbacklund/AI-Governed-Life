import os
from unittest.mock import patch

import pytest

from config import load_config

REQUIRED_ENV = {
    "GCP_PROJECT_ID": "GeneralOrder",
    "TELEGRAM_BOT_TOKEN": "123:ABC",
    "TELEGRAM_CHAT_ID": "999",
    "TAVILY_API_KEY": "tvly-test",
}


@patch("config.load_dotenv")
class TestLoadConfig:

    def test_load_config_all_required(self, _dotenv):
        with patch.dict(os.environ, REQUIRED_ENV, clear=True):
            cfg = load_config()
        assert cfg.gcp_project_id == "GeneralOrder"
        assert cfg.gcp_region == "global"
        assert cfg.telegram_bot_token == "123:ABC"
        assert cfg.telegram_chat_id == 999
        assert isinstance(cfg.telegram_chat_id, int)
        assert cfg.tavily_api_key == "tvly-test"

    def test_load_config_missing_key(self, _dotenv):
        env = {k: v for k, v in REQUIRED_ENV.items() if k != "GCP_PROJECT_ID"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="GCP_PROJECT_ID"):
                load_config()

    def test_load_config_empty_key(self, _dotenv):
        env = {**REQUIRED_ENV, "TELEGRAM_BOT_TOKEN": ""}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
                load_config()

    def test_load_config_defaults(self, _dotenv):
        with patch.dict(os.environ, REQUIRED_ENV, clear=True):
            cfg = load_config()
        assert cfg.timezone == "Europe/Amsterdam"
        assert cfg.default_location == "Eindhoven"
        assert cfg.default_lat == 51.4416
        assert cfg.default_lon == 5.4697
        assert cfg.model == "gemini-3.1-pro-preview"
        assert cfg.max_tokens == 4096
        assert cfg.token_threshold == 80000

    def test_load_config_invalid_chat_id(self, _dotenv):
        env = {**REQUIRED_ENV, "TELEGRAM_CHAT_ID": "abc"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError):
                load_config()
