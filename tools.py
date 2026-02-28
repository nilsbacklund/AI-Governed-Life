import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from google.genai import types

from weather import fetch_weather
from search import web_search

# --- Tool declarations (Google GenAI format) ---

TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="send_message",
        description="Send a message to the user via Telegram. Use this to deliver plans, reminders, answers, and nudges. Supports text, images, or both (image with text caption). At least one of text or image_url must be provided. Do NOT use this to talk to yourself — only for messages the user should see.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Message text. Supports Telegram markdown. Required if no image_url. E.g. 'Time to leave for ASML — 25 min bike ride.'"
                },
                "image_url": {
                    "type": "string",
                    "description": "URL of an image to send. If text is also provided, it becomes the caption. Required if no text."
                }
            },
            "required": []
        },
    ),
    types.FunctionDeclaration(
        name="read_file",
        description="Read a file from the data directory. Use to check any file you've created — memory, plans, profile info, etc. Check the 'Available files' listing in your system prompt to see what exists. Do NOT guess paths — only read files from the listing.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from the data directory, as shown in 'Available files'. E.g. 'memory.md', 'groceries.md', 'training.md'."
                }
            },
            "required": ["path"]
        },
    ),
    types.FunctionDeclaration(
        name="write_file",
        description="Write or overwrite a file in the data directory. You decide what files to create and how to organize them. Replaces the entire file — use read_file first to preserve content, or edit_file for small changes. Also creates new files and parent directories.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from the data directory. E.g. 'plan.md', 'profile/wardrobe.md'."
                },
                "content": {
                    "type": "string",
                    "description": "The complete file content. This replaces the entire file."
                }
            },
            "required": ["path", "content"]
        },
    ),
    types.FunctionDeclaration(
        name="edit_file",
        description="Apply a targeted text replacement in a file. Use this for small changes to large files — e.g. updating one time block in the plan, or adding a line to memory. Do NOT use if you need to restructure the whole file — use write_file for that. old_text must match exactly including whitespace.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from the data directory. File must exist."
                },
                "old_text": {
                    "type": "string",
                    "description": "Exact text to find in the file. Must be unique. Copy exactly as it appears."
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement text. Can be longer, shorter, or empty (to delete)."
                }
            },
            "required": ["path", "old_text", "new_text"]
        },
    ),
    types.FunctionDeclaration(
        name="set_next_wakeup",
        description="Schedule when to wake up next without a user message. Use for proactive check-ins, reminders, and timed nudges. A timer should always be active so you don't lose track of the user. Only one timer active at a time — calling this replaces any existing timer. Timer persists when user messages arrive; only replaced by calling this tool again.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "time": {
                    "type": "string",
                    "description": "When to wake up. ISO time for today ('14:30'), full datetime ('2026-02-22T07:00'), or relative ('+45m', '+2h')."
                },
                "reason": {
                    "type": "string",
                    "description": "Why you are waking up, shown to you when timer fires. Be specific: 'Check if user has left for ASML', not 'check in'."
                }
            },
            "required": ["time", "reason"]
        },
    ),
    types.FunctionDeclaration(
        name="get_weather",
        description="Get current weather and forecast. Use for clothing advice (jacket? umbrella?), commute planning (rain during bike ride?), and activity suggestions. Do NOT call more than once per hour unless location changed.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name ('Eindhoven') or coordinates ('51.44,5.47'). Defaults to home location."
                },
                "forecast_hours": {
                    "type": "integer",
                    "description": "Hours ahead to forecast. Default: 12. Use 3 if you only need the next few hours."
                }
            },
            "required": []
        },
    ),
    types.FunctionDeclaration(
        name="web_search",
        description="Search the web for information not in your files. Use for recipes, store hours, prices, transit, events. Do NOT use for info already in profile files — read those instead. Be specific in queries for better results.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Be specific: 'quick chicken broccoli rice recipe 30 minutes' not 'dinner recipe'."
                }
            },
            "required": ["query"]
        },
    ),
]


# --- Dependency injection for telegram send ---

_send_fn = None
_config = None
_timer = None


def init_tools(send_fn, config, timer):
    global _send_fn, _config, _timer
    _send_fn = send_fn
    _config = config
    _timer = timer


def _sanitize_path(path_str: str) -> Path:
    """Prevent directory traversal outside data dir."""
    p = Path(path_str)
    # Resolve against data_dir and check it's still inside
    resolved = (_config.data_dir / p).resolve()
    data_resolved = _config.data_dir.resolve()
    if not str(resolved).startswith(str(data_resolved)):
        raise ValueError(f"Path escapes data directory: {path_str}")
    return p


async def execute_tool(name: str, args: dict) -> dict:
    try:
        match name:
            case "send_message":
                text = args.get("text")
                image_url = args.get("image_url")
                if not text and not image_url:
                    return {"error": "At least one of text or image_url must be provided.", "is_error": True}
                await _send_fn(text=text, image_url=image_url)
                return {"status": "sent", "timestamp": datetime.now(ZoneInfo(_config.timezone)).isoformat()}

            case "read_file":
                path = _sanitize_path(args["path"])
                full = _config.data_dir / path
                if not full.exists():
                    return {"error": f"File not found: {args['path']}. Check the Available files listing for valid paths.", "is_error": True}
                return {"content": full.read_text()}

            case "write_file":
                path = _sanitize_path(args["path"])
                full = _config.data_dir / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(args["content"])
                return {"status": "written", "path": args["path"]}

            case "edit_file":
                path = _sanitize_path(args["path"])
                full = _config.data_dir / path
                if not full.exists():
                    return {"error": f"File not found: {args['path']}.", "is_error": True}
                content = full.read_text()
                old = args["old_text"]
                if old not in content:
                    return {"error": f"old_text not found in {args['path']}. Use read_file to check current content.", "is_error": True}
                content = content.replace(old, args["new_text"], 1)
                full.write_text(content)
                return {"status": "edited", "path": args["path"]}

            case "set_next_wakeup":
                wakeup_time = _timer.parse_time(args["time"])
                reason = args["reason"]
                _timer.schedule(wakeup_time, reason)
                return {"status": "scheduled", "wakeup_at": wakeup_time.isoformat(), "reason": reason}

            case "get_weather":
                location = args.get("location", _config.default_location)
                hours = args.get("forecast_hours", 12)
                # Parse coordinates if given as "lat,lon"
                if "," in str(location):
                    parts = location.split(",")
                    lat, lon = float(parts[0].strip()), float(parts[1].strip())
                else:
                    lat, lon = _config.default_lat, _config.default_lon
                return await fetch_weather(lat, lon, hours)

            case "web_search":
                results = await web_search(args["query"], _config.tavily_api_key)
                return {"results": results[:5]}

            case _:
                return {"error": f"Unknown tool: {name}", "is_error": True}

    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "is_error": True}
