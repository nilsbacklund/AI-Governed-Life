import ast
import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from search import web_search

# --- Tool declarations (OpenAI function-calling format) ---

TOOL_DECLARATIONS = [
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send a message to the user via Telegram. Use this to deliver plans, reminders, answers, and nudges. Supports text, images, or both (image with text caption). At least one of text or image_url must be provided. Do NOT use this to talk to yourself — only for messages the user should see.",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the data directory. Use to check any file you've created — memory, plans, profile info, etc. Check the 'Available files' listing in your system prompt to see what exists. Do NOT guess paths — only read files from the listing. Files sent via Telegram are saved to inbox/. Text files (.csv, .txt, .md, .json) can be read directly. Binary files return metadata — use a plugin to parse them (install needed packages with install_package first).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from the data directory, as shown in 'Available files'. E.g. 'memory.md', 'groceries.md', 'training.md'."
                    }
                },
                "required": ["path"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or overwrite a file in the data directory. You decide what files to create and how to organize them. Replaces the entire file — use read_file first to preserve content, or edit_file for small changes. Also creates new files and parent directories. Store API keys and credentials in secrets/ (e.g. secrets/api_key.txt). Never include secret values in messages — just confirm storage.",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Apply a targeted text replacement in a file. Use this for small changes to large files — e.g. updating one time block in the plan, or adding a line to memory. Do NOT use if you need to restructure the whole file — use write_file for that. old_text must match exactly including whitespace.",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_next_wakeup",
            "description": "Schedule when to wake up next without a user message. Use for proactive check-ins, reminders, and timed nudges. A timer should always be active so you don't lose track of the user. Only one timer active at a time — calling this replaces any existing timer. Timer persists when user messages arrive; only replaced by calling this tool again.",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information not in your files. Use for recipes, store hours, prices, transit, events. Do NOT use for info already in profile files — read those instead. Be specific in queries for better results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query. Be specific: 'quick chicken broccoli rice recipe 30 minutes' not 'dinner recipe'."
                    }
                },
                "required": ["query"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_integration",
            "description": "Call a plugin action. Use this for all integrations (weather, and any plugins you've created). Check the 'Available Integrations' section in your system prompt to see loaded plugins and their actions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name (e.g. 'weather', 'echo')."
                    },
                    "action": {
                        "type": "string",
                        "description": "Action to call on the plugin (e.g. 'get_forecast')."
                    },
                    "params": {
                        "type": "object",
                        "description": "Parameters to pass to the action. Each plugin documents its expected params."
                    }
                },
                "required": ["name", "action"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_plugin",
            "description": "Create or update a plugin at runtime. Write Python code that will be saved to plugins/<name>.py and hot-loaded. The plugin must expose: PLUGIN_NAME (str), ACTIONS (dict), and an async call(action, params) function returning a dict (not a string). Optionally expose PLUGIN_DESCRIPTION (str) and async setup(config). For HTTP requests, use httpx.AsyncClient (already installed) — never urllib.request. Study existing plugins with read_file as reference. After writing, all declared actions are auto-tested with empty params — the results tell you which work and which need fixing. When auto-tests report errors, fix the code and call write_plugin again — keep iterating until it works. Dangerous imports (subprocess, shutil, sys, ctypes, os) are blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Plugin name (will be saved as plugins/<name>.py). Use snake_case."
                    },
                    "code": {
                        "type": "string",
                        "description": "Complete Python source code for the plugin."
                    }
                },
                "required": ["name", "code"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_package",
            "description": "Install Python packages from PyPI. Use this when a plugin needs a library that isn't installed yet. Only accepts package names (not URLs or local paths). Supports version specifiers like 'openpyxl>=3.0'. Multiple packages can be space-separated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "packages": {
                        "type": "string",
                        "description": "Space-separated package names to install. E.g. 'openpyxl' or 'fitparse garmin-fit-sdk' or 'google-api-python-client>=2.0'."
                    }
                },
                "required": ["packages"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reaction",
            "description": "React to a Telegram message with an emoji. Use the msg:ID shown in messages to reference which message to react to. Common emojis: \ud83d\udc4d \ud83d\udc4e \u2764\ufe0f \ud83d\udd25 \ud83c\udf89 \ud83d\ude02 \ud83d\ude2e \ud83d\ude22 \ud83d\ude4f \ud83d\udc40",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "The message ID to react to (from msg:ID prefix in messages)."
                    },
                    "emoji": {
                        "type": "string",
                        "description": "The emoji to react with. E.g. '\ud83d\udc4d', '\u2764\ufe0f', '\ud83d\udd25'."
                    }
                },
                "required": ["message_id", "emoji"]
            },
        },
    },
]


# --- Dependency injection ---

_send_fn = None
_config = None
_timer = None
_plugin_registry = None
_react_fn = None


def init_tools(send_fn, config, timer, plugin_registry=None, react_fn=None):
    global _send_fn, _config, _timer, _plugin_registry, _react_fn
    _send_fn = send_fn
    _config = config
    _timer = timer
    _plugin_registry = plugin_registry
    _react_fn = react_fn


# --- Blocked imports for write_plugin safety ---

_BLOCKED_IMPORTS = frozenset({
    "subprocess", "shutil", "sys", "ctypes", "os",
    "importlib", "code", "codeop", "compileall",
    "multiprocessing", "signal", "socket",
})


def _fresh_import(path: Path, name: str):
    """Import a module from file, bypassing all caches."""
    from types import ModuleType
    source = path.read_text()
    module = ModuleType(f"plugins.{name}")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def _check_imports(code: str) -> list[str]:
    """Parse code with ast and return list of blocked imports found."""
    tree = ast.parse(code)
    blocked = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _BLOCKED_IMPORTS:
                    blocked.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in _BLOCKED_IMPORTS:
                    blocked.append(node.module)
    return blocked


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
                message_id = await _send_fn(text=text, image_url=image_url)
                result = {"status": "sent", "timestamp": datetime.now(ZoneInfo(_config.timezone)).isoformat()}
                if message_id is not None:
                    result["message_id"] = message_id
                return result

            case "read_file":
                path = _sanitize_path(args["path"])
                full = _config.data_dir / path
                if not full.exists():
                    return {"error": f"File not found: {args['path']}. Check the Available files listing for valid paths.", "is_error": True}
                try:
                    return {"content": full.read_text(encoding="utf-8")}
                except (UnicodeDecodeError, ValueError):
                    size = full.stat().st_size
                    return {
                        "binary": True,
                        "path": args["path"],
                        "size_bytes": size,
                        "note": "Binary file — use a plugin to parse this format. Install needed packages with install_package first.",
                    }

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

            case "web_search":
                results = await web_search(args["query"], _config.tavily_api_key)
                return {"results": results[:5]}

            case "call_integration":
                plugin_name = args["name"]
                action = args["action"]
                params = args.get("params", {})
                if _plugin_registry is None:
                    return {"error": "Plugin registry not initialized.", "is_error": True}
                try:
                    result = await _plugin_registry.call(plugin_name, action, params)
                    # Ensure result is always a dict (plugins may return strings, lists, etc.)
                    if not isinstance(result, dict):
                        result = {"result": result}
                    if "error" in result:
                        result["is_error"] = True
                    return result
                except Exception as e:
                    return {"error": f"{type(e).__name__}: {e}", "plugin": plugin_name, "action": action, "is_error": True}

            case "write_plugin":
                return await _execute_write_plugin(args["name"], args["code"])

            case "install_package":
                return await _execute_install_package(args["packages"])

            case "set_reaction":
                if _react_fn is None:
                    return {"error": "Reaction function not initialized.", "is_error": True}
                await _react_fn(args["message_id"], args["emoji"])
                return {"status": "reacted", "message_id": args["message_id"], "emoji": args["emoji"]}

            case _:
                return {"error": f"Unknown tool: {name}", "is_error": True}

    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "is_error": True}


async def _execute_write_plugin(name: str, code: str) -> dict:
    """Write, validate, auto-test, and hot-load a plugin."""
    from plugins import PLUGINS_DIR

    # 1. Safety check: parse and block dangerous imports
    try:
        blocked = _check_imports(code)
    except SyntaxError as e:
        # Write the file anyway so the agent can read and fix it
        plugin_path = PLUGINS_DIR / f"{name}.py"
        plugin_path.write_text(code)
        return {
            "status": "syntax_error",
            "error": f"SyntaxError: {e}",
            "path": str(plugin_path),
            "loaded": False,
            "is_error": True,
        }

    if blocked:
        return {
            "status": "blocked_imports",
            "error": f"Blocked imports found: {blocked}. These are not allowed for security reasons.",
            "loaded": False,
            "is_error": True,
        }

    # 2. Write the code to plugins/<name>.py
    plugin_path = PLUGINS_DIR / f"{name}.py"
    plugin_path.write_text(code)

    # 3. Validate: import and check interface
    try:
        module = _fresh_import(plugin_path, name)
    except Exception as e:
        return {
            "status": "import_error",
            "error": f"{type(e).__name__}: {e}",
            "path": str(plugin_path),
            "loaded": False,
            "is_error": True,
        }

    for attr in ("PLUGIN_NAME", "ACTIONS", "call"):
        if not hasattr(module, attr):
            return {
                "status": "validation_error",
                "error": f"Plugin missing required attribute: {attr}",
                "path": str(plugin_path),
                "loaded": False,
                "is_error": True,
            }

    # 4. Auto-test: call every declared action with empty params
    actions_tested = {}
    for action_name in module.ACTIONS:
        try:
            await module.call(action_name, {})
            actions_tested[action_name] = "ok"
        except Exception as e:
            actions_tested[action_name] = f"{type(e).__name__}: {e}"

    # 5. Run setup if available
    if hasattr(module, "setup") and _config:
        try:
            await module.setup(_config)
        except Exception as e:
            actions_tested["_setup"] = f"{type(e).__name__}: {e}"

    # 6. Hot-load into registry
    if _plugin_registry is not None:
        _plugin_registry.unregister(name)
        _plugin_registry.register(module.PLUGIN_NAME, module)

    return {
        "status": "loaded",
        "plugin": module.PLUGIN_NAME,
        "actions_tested": actions_tested,
        "path": str(plugin_path),
        "loaded": True,
    }


# --- Package installation ---

_PACKAGE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*([<>=!~]+[a-zA-Z0-9_.*]+)?$")


async def _execute_install_package(packages_str: str) -> dict:
    """Validate and install Python packages from PyPI."""
    packages = packages_str.split()
    if not packages:
        return {"error": "No package names provided.", "is_error": True}

    # Validate each package name
    invalid = [p for p in packages if not _PACKAGE_RE.match(p)]
    if invalid:
        return {
            "error": f"Invalid package names: {invalid}. Only PyPI package names are allowed (no URLs, paths, or flags).",
            "is_error": True,
        }

    # Run pip install
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", "install", "--no-input", *packages,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace").strip()

    if proc.returncode != 0:
        return {
            "status": "failed",
            "packages": packages,
            "output": output[-2000:],  # Truncate long output
            "is_error": True,
        }

    return {
        "status": "installed",
        "packages": packages,
        "output": output[-1000:],
    }
