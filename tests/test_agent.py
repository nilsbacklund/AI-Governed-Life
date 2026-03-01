import asyncio
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from agent import Agent, _serialize_response
from config import Config
from timer import WakeupTimer


@pytest.fixture
def tz():
    return ZoneInfo("Europe/Amsterdam")


@pytest.fixture
def agent_setup(tmp_path, tz):
    config = Config(
        telegram_bot_token="test-bot",
        telegram_chat_id=123,
        tavily_api_key="test-tavily",
        timezone="Europe/Amsterdam",
        default_location="Eindhoven",
        default_lat=51.4416,
        default_lon=5.4697,
        model="vertex_ai_beta/gemini-3.1-pro-preview",
        max_tokens=4096,
        token_threshold=80000,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        history_file=tmp_path / "history.json",
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    timer = WakeupTimer(tz)
    queue = asyncio.Queue()
    logger = MagicMock()
    agent = Agent(config, timer, queue, logger)
    return agent, config, timer, queue


def _tool_call(name, args, call_id="call_1"):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _make_response(content=None, tool_calls=None, usage=None):
    """Build a mock LiteLLM response in OpenAI format."""
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage or SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        _hidden_params={"response_cost": 0},
    )


class TestTurnExecution:

    @patch("agent.build_system_prompt", return_value="system prompt")
    @patch("agent.litellm")
    async def test_turn_no_tools_with_timer(self, mock_litellm, _prompt, agent_setup, tz):
        agent, config, timer, queue = agent_setup
        # Pre-set timer so no nudge happens
        timer.schedule(datetime.now(tz) + timedelta(hours=1), "existing timer")

        mock_litellm.acompletion = AsyncMock(return_value=_make_response(
            content="Good morning!"
        ))

        trigger = {
            "kind": "TIMER",
            "detail": "test",
            "content": [{"type": "text", "text": "[TIMER] test"}],
        }
        await agent._run_turn(trigger)

        # Should have made exactly 1 API call
        assert mock_litellm.acompletion.await_count == 1
        # Conversation should have 2 new messages: user trigger + assistant response
        assert len(agent._conversation) == 2
        assert agent._conversation[0]["role"] == "user"
        assert agent._conversation[1]["role"] == "assistant"

    @patch("agent.build_system_prompt", return_value="system prompt")
    @patch("agent.execute_tool", new_callable=AsyncMock)
    @patch("agent.litellm")
    async def test_turn_with_tool_call(self, mock_litellm, mock_exec, _prompt, agent_setup, tz):
        agent, config, timer, queue = agent_setup
        timer.schedule(datetime.now(tz) + timedelta(hours=1), "existing timer")

        mock_exec.return_value = {"status": "sent"}

        # First API call returns tool_call, second returns text
        mock_litellm.acompletion = AsyncMock(side_effect=[
            _make_response(
                tool_calls=[_tool_call("send_message", {"text": "hi"})]
            ),
            _make_response(
                content="Done!"
            ),
        ])

        trigger = {
            "kind": "USER",
            "detail": "send hi",
            "content": [{"type": "text", "text": "send hi"}],
        }
        await agent._run_turn(trigger)

        assert mock_litellm.acompletion.await_count == 2
        mock_exec.assert_awaited_once_with("send_message", {"text": "hi"})

    @patch("agent.build_system_prompt", return_value="system prompt")
    @patch("agent.litellm")
    async def test_nudge_when_no_timer(self, mock_litellm, _prompt, agent_setup, tz):
        agent, config, timer, queue = agent_setup
        # No timer set — agent should nudge

        mock_litellm.acompletion = AsyncMock(return_value=_make_response(
            content="I'll check in later"
        ))

        trigger = {
            "kind": "TIMER",
            "detail": "test",
            "content": [{"type": "text", "text": "[TIMER] test"}],
        }
        await agent._run_turn(trigger)

        # 1 initial + 3 nudges = 4 API calls
        assert mock_litellm.acompletion.await_count == 4
        # After 3 nudges, timer should have been force-set
        assert timer.is_active()
        assert "auto-scheduled" in timer.reason

    @patch("agent.build_system_prompt", return_value="system prompt")
    @patch("agent.litellm")
    async def test_reflection_trigger_skips_nudge(self, mock_litellm, _prompt, agent_setup, tz):
        """REFLECTION triggers should NOT nudge about missing timers."""
        agent, config, timer, queue = agent_setup
        # No timer set — but REFLECTION should skip nudging

        mock_litellm.acompletion = AsyncMock(return_value=_make_response(
            content="Nothing to do right now."
        ))

        trigger = {
            "kind": "REFLECTION",
            "detail": "self-improvement check",
            "content": [{"type": "text", "text": "[SELF-REFLECTION] What can you do to improve?"}],
        }
        await agent._run_turn(trigger)

        # Only 1 API call — no nudges
        assert mock_litellm.acompletion.await_count == 1
        # Timer should NOT have been force-set
        assert not timer.is_active()


class TestReflectionInterval:

    def test_interval_recent_activity(self, agent_setup):
        agent, _, _, _ = agent_setup
        # Last activity just happened
        agent._last_activity_time = time.monotonic()
        interval = agent._compute_reflection_interval()
        assert interval == 10 * 60  # 10 minutes

    def test_interval_moderate_activity(self, agent_setup):
        agent, _, _, _ = agent_setup
        # Last activity 45 minutes ago
        agent._last_activity_time = time.monotonic() - 45 * 60
        interval = agent._compute_reflection_interval()
        assert interval == 30 * 60  # 30 minutes

    def test_interval_idle(self, agent_setup):
        agent, _, _, _ = agent_setup
        # Last activity 3 hours ago
        agent._last_activity_time = time.monotonic() - 3 * 60 * 60
        interval = agent._compute_reflection_interval()
        assert interval == 60 * 60  # 60 minutes

    def test_interval_boundary_30min(self, agent_setup):
        agent, _, _, _ = agent_setup
        # Exactly 30 minutes — should be 30min interval
        agent._last_activity_time = time.monotonic() - 30 * 60
        interval = agent._compute_reflection_interval()
        assert interval == 30 * 60

    def test_interval_boundary_2h(self, agent_setup):
        agent, _, _, _ = agent_setup
        # Exactly 2 hours — should be 60min interval
        agent._last_activity_time = time.monotonic() - 2 * 60 * 60
        interval = agent._compute_reflection_interval()
        assert interval == 60 * 60


class TestSerialization:

    def test_text_response(self):
        response = _make_response(content="hello")
        serialized = _serialize_response(response)
        assert serialized == {"role": "assistant", "content": "hello"}

    def test_tool_call_response(self):
        response = _make_response(
            tool_calls=[_tool_call("send_message", {"text": "hi"}, call_id="call_abc")]
        )
        serialized = _serialize_response(response)
        assert serialized["role"] == "assistant"
        assert isinstance(serialized["content"], dict)
        assert serialized["content"]["tool_calls"][0]["id"] == "call_abc"
        assert serialized["content"]["tool_calls"][0]["function"]["name"] == "send_message"


class TestHistoryPersistence:

    async def test_save_load_roundtrip(self, agent_setup):
        agent, config, timer, queue = agent_setup
        agent._conversation = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "content": "hi"},
        ]
        agent.save_history()

        # Load on a new agent
        agent2 = Agent(config, timer, queue, MagicMock())
        agent2.load_history()
        assert agent2._conversation == agent._conversation

    async def test_load_no_history_file(self, agent_setup):
        agent, config, timer, queue = agent_setup
        # No history file exists
        agent.load_history()
        assert agent._conversation == []

    async def test_load_old_format_clears_history(self, agent_setup):
        """Old history without version marker should be cleared."""
        agent, config, timer, queue = agent_setup
        # Write old-format history (no version)
        config.history_file.write_text(json.dumps({
            "messages": [{"role": "user", "content": "old"}]
        }))
        agent.load_history()
        assert agent._conversation == []
