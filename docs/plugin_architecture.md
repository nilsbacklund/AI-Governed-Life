# Plugin Architecture — Design Notes

> Status: Draft / Design only — no code yet.

## Problem

Every new integration (Strava, Spotify, Home Assistant, etc.) currently means adding a new `FunctionDeclaration` to `TOOL_DECLARATIONS` and a new `case` branch in `execute_tool`. This doesn't scale:
- The agent's tool list grows linearly — more tokens, more confusion.
- Each integration leaks into `tools.py` instead of living in its own file.
- No way to compose integrations into higher-level workflows.

## 1. Unified Integration Tool

Instead of N tools, expose **one** routing tool to the agent:

```
call_integration(name, action, params)
```

The agent calls it like:
```json
{"name": "strava", "action": "get_activities", "params": {"count": 5}}
{"name": "home_assistant", "action": "turn_on", "params": {"entity": "light.desk"}}
```

A single `FunctionDeclaration` for `call_integration` replaces all per-integration declarations. The system prompt lists available integrations and their actions so the agent knows what's possible.

### Tool declaration sketch

```python
types.FunctionDeclaration(
    name="call_integration",
    description="Call an external integration. See system prompt for available integrations and actions.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "name":   {"type": "string", "description": "Integration name, e.g. 'strava', 'spotify'."},
            "action": {"type": "string", "description": "Action to perform, e.g. 'get_activities'."},
            "params": {"type": "object", "description": "Action-specific parameters."},
        },
        "required": ["name", "action"],
    },
)
```

### Routing in `execute_tool`

```python
case "call_integration":
    return await plugin_registry.call(args["name"], args["action"], args.get("params", {}))
```

## 2. Plugin Interface

Each plugin lives in `plugins/<name>.py` and exposes a standard interface.

```python
# plugins/strava.py

PLUGIN_NAME = "strava"
PLUGIN_DESCRIPTION = "Strava running/cycling tracker"

ACTIONS = {
    "get_activities": {
        "description": "Get recent activities",
        "params": {"count": "int, default 10"},
    },
    "get_stats": {
        "description": "Get all-time / yearly stats",
        "params": {},
    },
}

async def setup(config: dict):
    """Called once on startup. Load tokens, validate credentials."""
    ...

async def call(action: str, params: dict) -> dict:
    """Dispatch an action. Return a dict (serializable to JSON)."""
    match action:
        case "get_activities":
            return await _get_activities(params.get("count", 10))
        case "get_stats":
            return await _get_stats()
        case _:
            return {"error": f"Unknown action: {action}"}
```

### Plugin registry

A `PluginRegistry` class in `plugins/__init__.py`:
- On startup, auto-discovers `plugins/*.py` files.
- Calls `setup(config)` on each.
- Exposes `call(name, action, params)` that routes to the right plugin.
- Generates a summary string injected into the system prompt so the agent knows what's available.

```python
class PluginRegistry:
    plugins: dict[str, module]

    async def load_all(self, config):
        for path in Path("plugins").glob("*.py"):
            ...

    async def call(self, name, action, params) -> dict:
        ...

    def prompt_summary(self) -> str:
        """Return markdown listing all plugins and their actions for the system prompt."""
        ...
```

## 3. Skills — Composable Workflows

A **skill** is a predefined multi-step workflow that chains tools and integrations. The agent can invoke a skill by name, or the user can trigger one directly.

Examples:
- **morning_briefing**: weather + strava yesterday stats + today's plan from `plan.md`
- **weekly_review**: strava weekly stats + goals check + write summary to `data/reviews/`
- **commute_check**: weather for next 2h + travel time

### Skill definition

Skills live in `plugins/skills/` as YAML or Python:

```yaml
# plugins/skills/morning_briefing.yaml
name: morning_briefing
description: "Morning overview: weather, fitness, and today's plan"
steps:
  - tool: get_weather
    args: {forecast_hours: 12}
    store_as: weather
  - tool: call_integration
    args: {name: strava, action: get_activities, params: {count: 1}}
    store_as: last_activity
  - tool: read_file
    args: {path: plan.md}
    store_as: plan
  - compose: |
      Build a morning briefing message from:
      - Weather: {{weather}}
      - Last activity: {{last_activity}}
      - Today's plan: {{plan}}
```

The `compose` step hands the collected data back to the LLM with a formatting instruction, which then calls `send_message` with the result.

### Skill tool declaration

```python
types.FunctionDeclaration(
    name="run_skill",
    description="Run a predefined multi-step workflow. See system prompt for available skills.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "skill": {"type": "string", "description": "Skill name, e.g. 'morning_briefing'."},
            "overrides": {"type": "object", "description": "Optional param overrides."},
        },
        "required": ["skill"],
    },
)
```

**Alternative**: skills could just be prompt snippets the agent knows about and executes as a sequence of normal tool calls — no special runtime needed. The YAML approach is more structured but adds complexity. Worth starting with the prompt-based approach and graduating to YAML if needed.

## 4. Code-Editing Agent — `write_plugin`

The agent can write new plugin files using the existing `write_file` tool (targeting `plugins/` instead of `data/`). A dedicated `write_plugin` tool adds guardrails:

```python
types.FunctionDeclaration(
    name="write_plugin",
    description="Create or update a plugin file. The plugin is validated and hot-loaded.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "name":    {"type": "string", "description": "Plugin name (becomes plugins/<name>.py)."},
            "code":    {"type": "string", "description": "Full Python source code."},
        },
        "required": ["name", "code"],
    },
)
```

Execution:
1. Write to `plugins/<name>.py`
2. Validate: import the module, check it exposes `PLUGIN_NAME`, `ACTIONS`, `call`
3. Hot-reload into the registry
4. Return success or validation errors

Guardrails:
- Sandbox: plugin code runs with restricted imports (no `os.system`, `subprocess`, etc.)
- Review mode: optionally require user confirmation via Telegram before activating

## 5. Strava as First Plugin

### OAuth2 flow
- Strava uses OAuth2 with refresh tokens.
- Store tokens in `data/secrets/strava_tokens.json` (gitignored).
- `setup()` loads tokens, refreshes if expired.
- If no tokens exist, send the user an auth URL via Telegram.

### Actions
| Action | Description | Params |
|--------|-------------|--------|
| `get_activities` | Recent activities | `count` (default 10), `after` (ISO date) |
| `get_stats` | Yearly / all-time stats | `athlete_id` (default: self) |
| `get_activity_detail` | Single activity with splits | `activity_id` |

### API endpoints
- `GET /athlete/activities` — list activities
- `GET /athletes/{id}/stats` — athlete stats
- `GET /activities/{id}` — activity detail

### Config
Add to `config.py`:
```python
strava_client_id: str
strava_client_secret: str
```

## 6. Implementation Phases

### Phase 1: Plugin infrastructure
- Create `plugins/` directory and `PluginRegistry`
- Add `call_integration` tool declaration and routing in `execute_tool`
- Inject plugin summary into system prompt via `build_system_prompt`
- Write a trivial test plugin (e.g. `plugins/echo.py`) to validate the plumbing

### Phase 2: Strava plugin
- Implement `plugins/strava.py` with OAuth2 token handling
- Add `get_activities`, `get_stats`, `get_activity_detail` actions
- Store tokens securely, handle refresh
- Test with real Strava data

### Phase 3: Skills system
- Decide on approach: prompt-based or YAML-based (start with prompt-based)
- Define `morning_briefing` as first skill
- Add `run_skill` tool or just prompt-engineer the agent to follow skill recipes

### Phase 4: `write_plugin` tool
- Enable the agent to create new plugins at runtime
- Add validation and sandboxing
- Test by having the agent write a simple plugin end-to-end

### Phase 5: More integrations
- Spotify, Home Assistant, Google Calendar, etc.
- Each follows the same `plugins/<name>.py` pattern
