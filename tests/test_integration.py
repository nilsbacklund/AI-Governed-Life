"""Live integration tests — hit real LLM API and Telegram.

Skipped automatically when credentials are not available:
- LLM tests skip if ADC credentials are not configured
- Telegram test skips independently if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are unset
"""

import json
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Module-level skip: no ADC credentials → skip LLM tests
# ---------------------------------------------------------------------------
_has_adc = False
try:
    import google.auth

    google.auth.default()
    _has_adc = True
except Exception:
    pass

pytestmark = pytest.mark.skipif(
    not _has_adc,
    reason="Live integration tests require ADC credentials (gcloud auth application-default login)",
)

# ---------------------------------------------------------------------------
# Telegram-specific skip
# ---------------------------------------------------------------------------
skip_no_telegram = pytest.mark.skipif(
    not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"),
    reason="TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID required for Telegram test",
)

# ---------------------------------------------------------------------------
# Imports used by the tests (after skip guard so missing deps don't explode)
# ---------------------------------------------------------------------------
import litellm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def model_name():
    return os.environ.get("MODEL", "vertex_ai_beta/gemini-3.1-pro-preview")


# ---------------------------------------------------------------------------
# Minimal tool definition for the tool-call round-trip test
# ---------------------------------------------------------------------------
_TEST_TOOL = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "Returns the current UTC time.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestLLMIntegration:

    async def test_basic_call(self, model_name):
        """Simple completion — no tools, just verify we get a response."""
        response = await litellm.acompletion(
            model=model_name,
            messages=[
                {"role": "user", "content": "Say hello."},
            ],
            max_tokens=256,
        )
        assert response.choices[0].message is not None, "Expected a message from LLM"

    async def test_tool_call_roundtrip(self, model_name):
        """Full tool-call round-trip: prompt → tool_call → tool result → text."""
        # --- Turn 1: prompt that forces a tool call ---
        response = await litellm.acompletion(
            model=model_name,
            messages=[
                {"role": "system", "content": "Always use the get_current_time tool to answer time questions. Never guess."},
                {"role": "user", "content": "What time is it right now?"},
            ],
            tools=[_TEST_TOOL],
            max_tokens=256,
        )

        tool_calls = response.choices[0].message.tool_calls
        assert tool_calls, "Expected LLM to return a tool call"
        assert tool_calls[0].function.name == "get_current_time"

        # --- Turn 2: send tool result back ---
        response2 = await litellm.acompletion(
            model=model_name,
            messages=[
                {"role": "system", "content": "Always use the get_current_time tool to answer time questions. Never guess."},
                {"role": "user", "content": "What time is it right now?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_calls[0].id,
                            "type": "function",
                            "function": {
                                "name": tool_calls[0].function.name,
                                "arguments": tool_calls[0].function.arguments,
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": tool_calls[0].id,
                    "content": json.dumps({"time": "2026-03-01T12:00:00Z"}),
                },
            ],
            tools=[_TEST_TOOL],
            max_tokens=256,
        )

        text = response2.choices[0].message.content
        assert text, "Expected text response after tool result round-trip"

    async def test_usage_metadata(self, model_name):
        """Verify usage tokens are populated in the response."""
        response = await litellm.acompletion(
            model=model_name,
            messages=[
                {"role": "user", "content": "Say one word."},
            ],
            max_tokens=16,
        )
        assert response.usage.prompt_tokens > 0, "Expected prompt_tokens > 0"
        assert response.usage.completion_tokens > 0, "Expected completion_tokens > 0"


@skip_no_telegram
class TestTelegramIntegration:

    async def test_telegram_send(self):
        """Send a real test message via the Telegram Bot API."""
        from telegram import Bot

        token = os.environ["TELEGRAM_BOT_TOKEN"]
        chat_id = int(os.environ["TELEGRAM_CHAT_ID"])

        bot = Bot(token=token)
        try:
            msg = await bot.send_message(chat_id=chat_id, text="[integration test] ping")
            assert msg.message_id, "Expected a message_id from Telegram"
        finally:
            await bot.shutdown()
