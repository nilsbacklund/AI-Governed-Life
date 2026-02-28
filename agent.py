import asyncio
import base64
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types

from config import Config
from logger import AgentLogger
from prompts import build_system_prompt
from timer import WakeupTimer
from tools import TOOL_DECLARATIONS, execute_tool


class Agent:
    def __init__(
        self,
        config: Config,
        timer: WakeupTimer,
        queue: asyncio.Queue,
        logger: AgentLogger,
    ):
        self._config = config
        self._timer = timer
        self._queue = queue
        self._logger = logger
        self._tz = ZoneInfo(config.timezone)
        self._client = genai.Client(
            vertexai=True,
            project=config.gcp_project_id,
            location=config.gcp_region,
        ).aio
        self._conversation: list[dict] = []

    def load_history(self):
        if self._config.history_file.exists():
            data = json.loads(self._config.history_file.read_text())
            self._conversation = data.get("messages", [])

    def save_history(self):
        self._config.history_file.write_text(
            json.dumps({"messages": self._conversation}, indent=2, default=str)
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

    async def _wait_for_trigger(self) -> dict:
        msg_task = asyncio.create_task(self._queue.get(), name="msg")
        timer_task = asyncio.create_task(self._timer.wait(), name="timer")

        done, pending = await asyncio.wait(
            [msg_task, timer_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        result = done.pop().result()
        if isinstance(result, str):
            # Timer returned a reason string
            trigger_text = f"[TIMER] Wakeup at {datetime.now(self._tz).strftime('%H:%M')}. Reason: {result}"
            trigger = {"kind": "TIMER", "detail": result, "content": [{"type": "text", "text": trigger_text}]}
        else:
            # Message — result is a list of content blocks
            trigger = {"kind": "USER", "detail": _extract_text(result), "content": result}

        # Drain extra messages from the queue
        while not self._queue.empty():
            extra = self._queue.get_nowait()
            trigger["content"].extend(extra)
            if trigger["kind"] == "TIMER":
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

        while True:
            call_num += 1
            system = build_system_prompt(self._config.data_dir, self._tz)

            # Build contents list as GenAI Content objects
            contents = _build_contents(self._conversation)

            t0 = time.monotonic()
            max_retries = 5
            response = None
            for attempt in range(1, max_retries + 1):
                try:
                    response = await asyncio.wait_for(
                        self._client.models.generate_content(
                            model=self._config.model,
                            contents=contents,
                            config=types.GenerateContentConfig(
                                system_instruction=system,
                                max_output_tokens=self._config.max_tokens,
                                tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
                                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                            ),
                        ),
                        timeout=60,
                    )
                    break
                except (asyncio.TimeoutError, Exception) as exc:
                    if attempt < max_retries:
                        delay = 10 * (2 ** (attempt - 1))  # 10s, 20s, 40s, 80s
                        logging.warning("Gemini API attempt %d/%d failed (%s), retrying in %ds", attempt, max_retries, exc, delay)
                        await asyncio.sleep(delay)
                    else:
                        logging.error("Gemini API failed after %d attempts: %s", max_retries, exc)
            if response is None:
                break
            latency_ms = int((time.monotonic() - t0) * 1000)

            # Serialize the response content to plain dicts for storage
            assistant_content = _serialize_response(response)
            self._conversation.append({"role": "assistant", "content": assistant_content})

            # Extract function calls
            function_calls = [
                part for part in (response.candidates[0].content.parts or [])
                if part.function_call
            ]

            # Log
            tc_log = [{"name": fc.function_call.name, "args": dict(fc.function_call.args or {})} for fc in function_calls]
            self._logger.log_api_call(call_num, trigger_info, response, tc_log, latency_ms)

            if not function_calls:
                # No tools — check if timer is active
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
            tool_results = []
            for fc_part in function_calls:
                fc = fc_part.function_call
                args = dict(fc.args or {})
                self._logger.log_tool_call(fc.name, _args_summary(args))
                result = await execute_tool(fc.name, args)
                is_error = result.pop("is_error", False)
                tool_results.append({
                    "type": "function_response",
                    "name": fc.name,
                    "response": result,
                    "is_error": is_error,
                })

            self._conversation.append({"role": "tool", "content": tool_results})

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
            content = msg["content"]
            if isinstance(content, str):
                text_parts.append(f"{role}: {content}")
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(f"{role}: {block['text']}")
                    elif isinstance(block, dict) and block.get("type") == "function_response":
                        text_parts.append(f"{role} (tool_result): {json.dumps(block.get('response', ''))}")

        summary_response = await self._client.models.generate_content(
            model=self._config.model,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text="\n".join(text_parts))])],
            config=types.GenerateContentConfig(
                system_instruction="Summarize this conversation into key facts, decisions, and current state. Be concise.",
                max_output_tokens=2000,
            ),
        )
        summary = summary_response.text

        self._conversation.clear()
        self._conversation.append({
            "role": "user",
            "content": [{"type": "text", "text": f"[CONTEXT SUMMARY]\n{summary}"}],
        })
        self._conversation.append({
            "role": "assistant",
            "content": [{"type": "text", "text": "Understood. I have the context from the summary and will continue from here."}],
        })
        self._conversation.extend(recent)
        self.save_history()


def _build_contents(conversation: list[dict]) -> list[types.Content]:
    """Convert stored conversation dicts to GenAI Content objects."""
    contents = []
    for msg in conversation:
        role = msg["role"]
        raw_content = msg["content"]

        if role == "user":
            parts = []
            if isinstance(raw_content, str):
                parts.append(types.Part.from_text(text=raw_content))
            elif isinstance(raw_content, list):
                for block in raw_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(types.Part.from_text(text=block["text"]))
                    elif isinstance(block, dict) and block.get("type") == "image":
                        source = block.get("source", {})
                        parts.append(types.Part.from_bytes(
                            data=base64.b64decode(source["data"]),
                            mime_type=source.get("media_type", "image/jpeg"),
                        ))
            contents.append(types.Content(role="user", parts=parts))

        elif role == "assistant" or role == "model":
            parts = []
            if isinstance(raw_content, str):
                parts.append(types.Part.from_text(text=raw_content))
            elif isinstance(raw_content, list):
                for block in raw_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(types.Part.from_text(text=block["text"]))
                    elif isinstance(block, dict) and block.get("type") == "function_call":
                        fc_part = types.Part(
                            function_call=types.FunctionCall(
                                name=block["name"],
                                args=block.get("args", {}),
                            ),
                        )
                        # Restore thought_signature required by Gemini 3.1
                        if block.get("thought_signature"):
                            fc_part.thought_signature = base64.b64decode(block["thought_signature"])
                        parts.append(fc_part)
            contents.append(types.Content(role="model", parts=parts))

        elif role == "tool":
            parts = []
            if isinstance(raw_content, list):
                for block in raw_content:
                    if isinstance(block, dict) and block.get("type") == "function_response":
                        parts.append(types.Part.from_function_response(
                            name=block["name"],
                            response=block.get("response", {}),
                        ))
            contents.append(types.Content(role="user", parts=parts))

    return contents


def _serialize_response(response) -> list[dict]:
    """Convert GenAI response parts to plain dicts for storage."""
    result = []
    parts = response.candidates[0].content.parts or []
    for part in parts:
        if part.function_call:
            d = {
                "type": "function_call",
                "name": part.function_call.name,
                "args": dict(part.function_call.args or {}),
            }
            # Gemini 3.1 requires thought_signature to be echoed back
            if getattr(part, "thought_signature", None):
                d["thought_signature"] = base64.b64encode(part.thought_signature).decode()
            result.append(d)
        elif part.text:
            result.append({"type": "text", "text": part.text})
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
    return total_chars // 4
