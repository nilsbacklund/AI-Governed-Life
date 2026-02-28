"""Live integration tests — hit real Gemini API and Telegram.

Skipped automatically when credentials are not available:
- All tests skip if GCP_PROJECT_ID is unset or google.auth.default() fails
- Telegram test skips independently if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are unset
"""

import base64
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Module-level skip: no GCP project or no ADC credentials → skip everything
# ---------------------------------------------------------------------------
_gcp_project = os.environ.get("GCP_PROJECT_ID", "")

_has_adc = False
try:
    import google.auth

    google.auth.default()
    _has_adc = True
except Exception:
    pass

pytestmark = pytest.mark.skipif(
    not _gcp_project or not _has_adc,
    reason="Live integration tests require GCP_PROJECT_ID and ADC credentials",
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
from google import genai
from google.genai import types


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def gemini_client():
    project = os.environ["GCP_PROJECT_ID"]
    location = os.environ.get("GCP_REGION", "global")
    return genai.Client(vertexai=True, project=project, location=location).aio


@pytest.fixture
def model_name():
    return os.environ.get("MODEL", "gemini-3.1-pro-preview")


# ---------------------------------------------------------------------------
# Minimal tool definition for the tool-call round-trip test
# ---------------------------------------------------------------------------
_TEST_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_current_time",
            description="Returns the current UTC time.",
            parameters=types.Schema(type="OBJECT", properties={}),
        )
    ]
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestGeminiIntegration:

    async def test_gemini_basic_call(self, gemini_client, model_name):
        """Simple generate_content — no tools, just verify we get text back."""
        response = await gemini_client.models.generate_content(
            model=model_name,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text="Say hello.")])],
            config=types.GenerateContentConfig(max_output_tokens=64),
        )
        assert response.text, "Expected non-empty text from Gemini"

    async def test_gemini_tool_call_roundtrip(self, gemini_client, model_name):
        """Full tool-call round-trip including thought_signature preservation.

        Mirrors the pattern in agent.py:258-268 (_build_contents) and
        agent.py:286-303 (_serialize_response).
        """
        # --- Turn 1: prompt that forces a tool call ---
        response = await gemini_client.models.generate_content(
            model=model_name,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="What time is it right now?")],
                ),
            ],
            config=types.GenerateContentConfig(
                system_instruction="Always use the get_current_time tool to answer time questions. Never guess.",
                max_output_tokens=256,
                tools=[_TEST_TOOL],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )

        # Find the function_call part
        fc_parts = [p for p in (response.candidates[0].content.parts or []) if p.function_call]
        assert fc_parts, "Expected Gemini to return a function_call part"

        fc_part = fc_parts[0]
        assert fc_part.function_call.name == "get_current_time"

        # Capture thought_signature (may or may not be present depending on model)
        thought_sig = getattr(fc_part, "thought_signature", None)

        # --- Rebuild model turn with thought_signature preserved ---
        model_fc_part = types.Part(
            function_call=types.FunctionCall(
                name=fc_part.function_call.name,
                args=dict(fc_part.function_call.args or {}),
            ),
        )
        if thought_sig:
            model_fc_part.thought_signature = thought_sig

        model_turn = types.Content(role="model", parts=[model_fc_part])

        # --- Turn 2: send function_response back ---
        tool_turn = types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="get_current_time",
                    response={"time": "2026-02-22T12:00:00Z"},
                ),
            ],
        )

        response2 = await gemini_client.models.generate_content(
            model=model_name,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="What time is it right now?")],
                ),
                model_turn,
                tool_turn,
            ],
            config=types.GenerateContentConfig(
                system_instruction="Always use the get_current_time tool to answer time questions. Never guess.",
                max_output_tokens=256,
                tools=[_TEST_TOOL],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )

        assert response2.text, "Expected text response after function_response round-trip"


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
