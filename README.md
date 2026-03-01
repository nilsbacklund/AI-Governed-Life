# AIBoss

An AI that bosses you around — managing your day, telling you what to do, and learning about you over time.

## What it does

AIBoss is a personal assistant agent that runs continuously via Telegram. It manages your schedule, checks in on you, gives you reminders, and adapts as it learns your habits. It uses [LiteLLM](https://github.com/BerriAI/litellm) for LLM access (100+ providers), defaulting to Gemini via Vertex AI, and communicates through Telegram messages.

**Core features:**
- Proactive check-ins and schedule management (wake up, work, train, eat, sleep)
- Persistent memory via a file system the agent organizes itself
- Weather lookups, web search, and file management tools
- Automatic context compaction for long-running conversations
- Cost tracking and detailed logging

**Plugin system:**
- Auto-discovers plugins from the `plugins/` directory at startup
- Agent can write and hot-load new plugins at runtime via `write_plugin`
- All plugins callable through a single `call_integration` tool
- AST-based safety checks block dangerous imports (subprocess, os, etc.)
- Auto-tests new plugins on write and reports which actions work
- Ships with `weather` and `echo` plugins as seed examples

**Self-improvement loop:**
- Periodic reflection triggers based on activity (10min / 30min / 60min intervals)
- Agent can decide to write plugins, update files, or prepare for upcoming events
- Silent by default — only reaches out when there's something actionable

## Setup

1. Clone the repo
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```
4. Required environment variables:
   - `VERTEXAI_PROJECT` — Google Cloud project with Vertex AI enabled
   - `VERTEXAI_LOCATION` — Vertex AI region (e.g. `global`)
   - `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
   - `TELEGRAM_CHAT_ID` — your Telegram chat ID
   - `TAVILY_API_KEY` — for web search

5. Authenticate with Google Cloud (for Vertex AI):
   ```bash
   gcloud auth application-default login
   ```

6. Optionally set `MODEL` in `.env` to use a different provider/model (see [LiteLLM docs](https://docs.litellm.ai/docs/providers)):
   ```bash
   MODEL=vertex_ai_beta/gemini-3.1-pro-preview   # default
   MODEL=vertex_ai/gemini-2.0-flash                # cheaper/faster
   MODEL=gpt-4o                                     # OpenAI (needs OPENAI_API_KEY)
   MODEL=claude-sonnet-4-20250514                    # Anthropic (needs ANTHROPIC_API_KEY)
   ```

7. Run:
   ```bash
   python3.11 main.py
   ```

## Project structure

```
├── main.py              # Entry point — wiring and startup
├── agent.py             # Main agent loop, conversation management, reflection triggers
├── tools.py             # Tool declarations and execution (including plugin tools)
├── prompts.py           # System prompt construction
├── config.py            # Configuration from environment
├── timer.py             # Wakeup timer scheduling
├── telegram_handler.py  # Telegram bot integration
├── weather.py           # Open-Meteo weather API
├── search.py            # Tavily web search
├── plugins/             # Plugin directory (auto-discovered at startup)
│   ├── __init__.py      # PluginRegistry class
│   ├── weather.py       # Weather plugin (wraps weather.py)
│   └── echo.py          # Minimal test plugin
├── tests/               # Test suite
├── data/                # Agent's persistent data files
├── logs/                # Runtime logs
└── docs/                # Architecture and design docs
```

## Tools

| Tool | Description |
|------|-------------|
| `send_message` | Send text/image messages via Telegram |
| `read_file` / `write_file` / `edit_file` | Manage files in the data directory |
| `set_next_wakeup` | Schedule the next timer-based wakeup |
| `web_search` | Search the web via Tavily |
| `call_integration` | Call any loaded plugin action |
| `write_plugin` | Create or update plugins at runtime |
| `install_package` | Install Python packages from PyPI |

## Testing

```bash
python3.11 -m pytest tests/ -x -v --ignore=tests/test_integration.py
```

## Tech stack

- Python 3.11+
- [LiteLLM](https://github.com/BerriAI/litellm) — unified API for 100+ LLM providers
- Default model: Gemini 2.5 Pro (via Vertex AI), swappable via `MODEL` env var
- python-telegram-bot
- Tavily Search API
- Open-Meteo Weather API
