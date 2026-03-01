from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def _cost(response) -> float:
    """Extract cost from LiteLLM response, falling back to zero."""
    hidden = getattr(response, "_hidden_params", None)
    if hidden and isinstance(hidden, dict):
        return hidden.get("response_cost", 0) or 0
    return 0


class AgentLogger:
    def __init__(self, logs_dir: Path, tz: ZoneInfo, model: str = ""):
        self._tz = tz
        self._model = model
        logs_dir.mkdir(parents=True, exist_ok=True)
        self._simple = open(logs_dir / "simple.log", "a")
        self._debug = open(logs_dir / "debug.log", "a")
        self._turn_calls: list[dict] = []

    def _now(self) -> str:
        return datetime.now(self._tz).strftime("%Y-%m-%d %H:%M:%S")

    def log_trigger(self, kind: str, detail: str):
        self._simple.write(f"[{self._now()}] {kind}: {detail}\n")
        self._simple.flush()
        self._turn_calls = []

    def log_tool_call(self, name: str, args_summary: str):
        self._simple.write(f"[{self._now()}] TOOL: {name}({args_summary})\n")
        self._simple.flush()

    def log_api_call(self, call_num: int, trigger_info: str, response, tool_calls: list, latency_ms: int):
        usage = response.usage
        cost = _cost(response)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0

        tools_str = ", ".join(
            f'{tc["name"]}({_short_args(tc["args"])})'
            for tc in tool_calls
        ) if tool_calls else "(none)"

        self._debug.write(
            f"[{self._now()}] API CALL #{call_num}\n"
            f"  trigger: {trigger_info}\n"
            f"  model: {self._model}\n"
            f"  input_tokens: {input_tokens}\n"
            f"  output_tokens: {output_tokens}\n"
            f"  tool_calls: [{tools_str}]\n"
            f"  latency_ms: {latency_ms}\n"
            f"  cost_estimate: ${cost:.4f}\n\n"
        )
        self._debug.flush()

        self._turn_calls.append({
            "input": input_tokens,
            "output": output_tokens,
            "cost": cost,
        })

    def log_turn_complete(self, conversation_length: int, timer_info: str):
        total_input = sum(c["input"] for c in self._turn_calls)
        total_output = sum(c["output"] for c in self._turn_calls)
        total_cost = sum(c["cost"] for c in self._turn_calls)
        self._debug.write(
            f"[{self._now()}] TURN COMPLETE\n"
            f"  total_api_calls: {len(self._turn_calls)}\n"
            f"  total_input_tokens: {total_input}\n"
            f"  total_output_tokens: {total_output}\n"
            f"  total_cost_estimate: ${total_cost:.4f}\n"
            f"  conversation_length: {conversation_length} messages\n"
            f"  active_timer: {timer_info}\n\n"
        )
        self._debug.flush()

    def close(self):
        self._simple.close()
        self._debug.close()


def _short_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 50:
            s = s[:47] + "..."
        parts.append(f'"{s}"' if isinstance(v, str) else s)
    return ", ".join(parts)
