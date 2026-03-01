import asyncio
import base64
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import litellm

from config import Config
from logger import AgentLogger
from prompts import build_system_prompt
from timer import WakeupTimer
from tools import TOOL_DECLARATIONS, execute_tool

# History format version — bump when conversation format changes
_HISTORY_VERSION = 2


class Agent:
    def __init__(
        self,
        config: Config,
        timer: WakeupTimer,
        queue: asyncio.Queue,
        logger: AgentLogger,
        plugin_registry=None,
    ):
        self._config = config
        self._timer = timer
        self._queue = queue
        self._logger = logger
        self._plugin_registry = plugin_registry
        self._tz = ZoneInfo(config.timezone)
        self._conversation: list[dict] = []
        self._last_activity_time: float = time.monotonic()

    def load_history(self):
        if self._config.history_file.exists():
            data = json.loads(self._config.history_file.read_text())
            # Check format version — clear incompatible old history
            if data.get("version") != _HISTORY_VERSION:
                logging.info("History format version mismatch — starting fresh")
                self._conversation = []
                return
            self._conversation = data.get("messages", [])

    def save_history(self):
        self._config.history_file.write_text(
            json.dumps({"version": _HISTORY_VERSION, "messages": self._conversation}, indent=2, default=str)
        )

    async def run(self):
        self.load_history()

        # Send an init message so the LLM gets a proper user turn to respond to
        now = datetime.now(self._tz)
        init_text = f"[SYSTEM] Agent started at {now.strftime('%H:%M on %A %Y-%m-%d')}. Plan the day or resume where you left off."
        self._queue.put_nowait([{"type": "text", "text": init_text}])

        while True:
            trigger = await self._wait_for_trigger()
            await self._run_turn(trigger)

    def _compute_reflection_interval(self) -> float:
        """Compute reflection poke interval based on time since last user activity."""
        elapsed = time.monotonic() - self._last_activity_time
        if elapsed < 30 * 60:       # < 30 min
            return 10 * 60           # poke every 10 min
        elif elapsed < 2 * 60 * 60:  # 30min–2h
            return 30 * 60           # poke every 30 min
        else:                        # > 2h
            return 60 * 60           # poke every 60 min

    async def _wait_for_trigger(self) -> dict:
        msg_task = asyncio.create_task(self._queue.get(), name="msg")
        timer_task = asyncio.create_task(self._timer.wait(), name="timer")
        reflection_interval = self._compute_reflection_interval()
        reflection_task = asyncio.create_task(
            asyncio.sleep(reflection_interval), name="reflection"
        )

        tasks = [msg_task, timer_task, reflection_task]
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        finished = done.pop()
        if finished is reflection_task:
            now_str = datetime.now(self._tz).strftime('%H:%M')
            trigger_text = (
                f"[SELF-REFLECTION] Wakeup at {now_str}. "
                "What can you do to improve right now? Consider: writing plugins for "
                "missing capabilities, updating your files, preparing for upcoming events, "
                "or checking on pending tasks."
            )
            trigger = {
                "kind": "REFLECTION",
                "detail": "self-improvement check",
                "content": [{"type": "text", "text": trigger_text}],
            }
        elif finished is timer_task:
            result = finished.result()
            trigger_text = f"[TIMER] Wakeup at {datetime.now(self._tz).strftime('%H:%M')}. Reason: {result}"
            trigger = {"kind": "TIMER", "detail": result, "content": [{"type": "text", "text": trigger_text}]}
        else:
            result = finished.result()
            self._last_activity_time = time.monotonic()
            trigger = {"kind": "USER", "detail": _extract_text(result), "content": result}

        # Drain extra messages from the queue
        while not self._queue.empty():
            extra = self._queue.get_nowait()
            trigger["content"].extend(extra)
            if trigger["kind"] in ("TIMER", "REFLECTION"):
                trigger["detail"] += " + messages"

        return trigger

    async def _run_turn(self, trigger: dict):
        self._logger.log_trigger(trigger["kind"], trigger["detail"])

        # Add trigger as a user message
        self._conversation.append({"role": "user", "content": trigger["content"]})

        trigger_info = f"{trigger['kind'].lower()} ({trigger['detail'][:60]})"
        call_num = 0
        nudge_count = 0
        max_nudges = 3
        is_reflection = trigger["kind"] == "REFLECTION"

        while True:
            call_num += 1
            system = build_system_prompt(self._config.data_dir, self._tz, self._plugin_registry)

            messages = _build_messages(self._conversation, system)

            t0 = time.monotonic()
            max_retries = 5
            response = None
            for attempt in range(1, max_retries + 1):
                try:
                    response = await asyncio.wait_for(
                        litellm.acompletion(
                            model=self._config.model,
                            messages=messages,
                            tools=TOOL_DECLARATIONS,
                            max_tokens=self._config.max_tokens,
                        ),
                        timeout=60,
                    )
                    break
                except (asyncio.TimeoutError, Exception) as exc:
                    if attempt < max_retries:
                        delay = 10 * (2 ** (attempt - 1))  # 10s, 20s, 40s, 80s
                        logging.warning("LiteLLM API attempt %d/%d failed (%s), retrying in %ds", attempt, max_retries, exc, delay)
                        await asyncio.sleep(delay)
                    else:
                        logging.error("LiteLLM API failed after %d attempts: %s", max_retries, exc)
            if response is None:
                break
            latency_ms = int((time.monotonic() - t0) * 1000)

            # Serialize the response to plain dicts for storage
            assistant_msg = _serialize_response(response)
            self._conversation.append(assistant_msg)

            # Extract function calls
            tool_calls = response.choices[0].message.tool_calls or []

            # Log
            tc_log = [{"name": tc.function.name, "args": json.loads(tc.function.arguments)} for tc in tool_calls]
            self._logger.log_api_call(call_num, trigger_info, response, tc_log, latency_ms)

            if not tool_calls:
                # No tools — check if timer is active (skip nudge for REFLECTION triggers)
                if not is_reflection:
                    if not self._timer.is_active() and nudge_count < max_nudges:
                        nudge_count += 1
                        self._conversation.append({
                            "role": "user",
                            "content": [{"type": "text", "text": "[SYSTEM] Heads up — there's no wakeup timer set. When should you check in next?"}],
                        })
                        continue
                    elif not self._timer.is_active():
                        # Force-set a default timer
                        wakeup_time = self._timer.parse_time("+30m")
                        self._timer.schedule(wakeup_time, "Default check-in (auto-scheduled)")
                break

            # Execute tools and collect results
            for tc in tool_calls:
                args = json.loads(tc.function.arguments)
                self._logger.log_tool_call(tc.function.name, _args_summary(args))
                result = await execute_tool(tc.function.name, args)
                # Guard against non-dict results
                if not isinstance(result, dict):
                    result = {"result": result}
                is_error = result.pop("is_error", False)
                self._conversation.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

        # Auto-compact if needed
        token_estimate = _estimate_tokens(self._conversation)
        if token_estimate > self._config.token_threshold:
            await self._compact()

        # Log turn complete
        timer_info = (
            f"{self._timer.wakeup_time.strftime('%H:%M')} ({self._timer.reason})"
            if self._timer.is_active()
            else "(none)"
        )
        self._logger.log_turn_complete(len(self._conversation), timer_info)

        self.save_history()

    async def _compact(self, keep_last_n: int = 10):
        if len(self._conversation) <= keep_last_n:
            return

        old = self._conversation[:-keep_last_n]
        recent = self._conversation[-keep_last_n:]

        # Format old messages as text for summarization
        text_parts = []
        for msg in old:
            role = msg["role"]
            content = msg.get("content", "")
            if isinstance(content, str):
                text_parts.append(f"{role}: {content}")
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(f"{role}: {block['text']}")

        summary_response = await litellm.acompletion(
            model=self._config.model,
            messages=[
                {"role": "system", "content": "Summarize this conversation into key facts, decisions, and current state. Be concise."},
                {"role": "user", "content": "\n".join(text_parts)},
            ],
            max_tokens=2000,
        )
        summary = summary_response.choices[0].message.content

        self._conversation.clear()
        self._conversation.append({
            "role": "user",
            "content": [{"type": "text", "text": f"[CONTEXT SUMMARY]\n{summary}"}],
        })
        self._conversation.append({
            "role": "assistant",
            "content": "Understood. I have the context from the summary and will continue from here.",
        })
        self._conversation.extend(recent)
        self.save_history()


def _build_messages(conversation: list[dict], system_prompt: str) -> list[dict]:
    """Convert stored conversation dicts to OpenAI message format."""
    messages = [{"role": "system", "content": system_prompt}]

    for msg in conversation:
        role = msg["role"]
        raw_content = msg.get("content", "")

        if role == "user":
            parts = []
            if isinstance(raw_content, str):
                parts.append({"type": "text", "text": raw_content})
            elif isinstance(raw_content, list):
                for block in raw_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append({"type": "text", "text": block["text"]})
                    elif isinstance(block, dict) and block.get("type") == "image":
                        source = block.get("source", {})
                        mime = source.get("media_type", "image/jpeg")
                        data = source["data"]
                        parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{data}"},
                        })
            if parts:
                messages.append({"role": "user", "content": parts})

        elif role in ("assistant", "model"):
            # Could be a string, a list of content blocks, or a dict with tool_calls
            if isinstance(raw_content, str):
                messages.append({"role": "assistant", "content": raw_content})
            elif isinstance(raw_content, dict):
                # Stored as {"content": ..., "tool_calls": [...]}
                m = {"role": "assistant", "content": raw_content.get("content")}
                if raw_content.get("tool_calls"):
                    m["tool_calls"] = raw_content["tool_calls"]
                messages.append(m)
            elif isinstance(raw_content, list):
                # Legacy format: list of content blocks (text + function_call)
                text_parts = []
                tool_calls = []
                for block in raw_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif isinstance(block, dict) and block.get("type") == "function_call":
                        tool_calls.append({
                            "id": block.get("id", f"call_{block['name']}"),
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block.get("args", {})),
                            },
                        })
                m = {"role": "assistant", "content": " ".join(text_parts) if text_parts else None}
                if tool_calls:
                    m["tool_calls"] = tool_calls
                messages.append(m)

        elif role == "tool":
            # New format: each tool result is its own message
            if isinstance(raw_content, str):
                # Already in new format (content is json string)
                messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", "unknown"),
                    "content": raw_content,
                })
            elif isinstance(raw_content, list):
                # Legacy format: list of function_response blocks
                for block in raw_content:
                    if isinstance(block, dict) and block.get("type") == "function_response":
                        messages.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_call_id", f"call_{block['name']}"),
                            "content": json.dumps(block.get("response", {})),
                        })

    return messages


def _serialize_response(response) -> dict:
    """Convert LiteLLM response to plain dict for conversation storage."""
    message = response.choices[0].message
    result = {"role": "assistant"}

    content = message.content
    tool_calls = message.tool_calls

    if tool_calls:
        result["content"] = {
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        }
    else:
        result["content"] = content or ""

    return result


def _extract_text(content: list[dict]) -> str:
    """Extract plain text from content blocks for logging."""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block["text"])
    text = " ".join(parts)
    return text[:100] if text else "(media)"


def _args_summary(args: dict) -> str:
    parts = []
    for v in args.values():
        s = json.dumps(v) if not isinstance(v, str) else f'"{v}"'
        if len(s) > 60:
            s = s[:57] + '..."'
        parts.append(s)
    return ", ".join(parts)


def _estimate_tokens(conversation: list[dict]) -> int:
    """Rough estimate: ~4 chars per token."""
    total_chars = 0
    for msg in conversation:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total_chars += len(json.dumps(block))
        elif isinstance(content, dict):
            total_chars += len(json.dumps(content))
    return total_chars // 4
