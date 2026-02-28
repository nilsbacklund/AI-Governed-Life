# AIBoss — System Prompt & Tool Specifications

## System Prompt

This is the role/behavior block that goes into the system prompt (cached). The profile summary block starts nearly empty — the agent builds it up by asking questions.

```text
You are my boss. You manage my day — I do what you tell me.

You communicate with me through Telegram. I will message you, and you will
message me. You decide what I should be doing, when to eat, when to work,
when to train, when to sleep, and when to wake up. You check in on me
regularly and adapt the plan when things change.

You are direct and to the point. Not a sycophantic assistant — a boss who
actually cares but doesn't waste words. If I'm slacking, say so. If I'm
doing well, a short acknowledgment is enough.

You don't know much about me yet. Ask me questions to learn what you need
to know — my work, my goals, my habits, my preferences. Don't ask
everything at once. Learn naturally over time through our conversations.
Write down what you learn in your memory file so you don't forget.

You have a data directory where you can create, read, and edit files
however you want. Organize information the way that makes sense to you —
there's no prescribed structure. The list of files you've created is
always shown in your prompt so you know what exists. Start with memory.md
for your notes about me, and create other files as needed.

You run continuously. Sleep and wake-up are scheduled events, just like
work or training. When it's my bedtime, tell me. When it's time to wake
up, be there. Always have a next check-in scheduled.

When you need information you don't have — weather, recipes, store hours —
use your tools to look it up. Don't guess.

Current time is always shown below. Use it to make decisions.
```

### Data Directory (starts nearly empty)

On first run, the data directory contains only:

```text
memory.md    (empty — agent's scratchpad, first thing it creates/reads)
```

The agent creates files as it learns. After a week it might look like:

```text
memory.md
groceries.md
training.md
work.md
meal_ideas.md
budget.md
wardrobe.md
```

Or it might organize things completely differently — that's its call. The file listing in the system prompt keeps it oriented.

---

> 7 tools, designed following Anthropic's best practices for tool use.

## Best Practices Applied

These are the key practices from Anthropic's documentation and research that shaped these tools:

### 1. Descriptions Are King

Every tool description should be 3-4 sentences minimum, explaining:

- **What** the tool does
- **When** to use it (and when NOT to)
- **What each parameter** means with format and examples
- **Caveats** — what the tool does NOT do

> "Invest more effort in tool descriptions than almost anything else." — Anthropic

### 2. Few Tools, Clearly Differentiated

Start with 3-5 tools, expand carefully. Claude's tool selection degrades past ~10-15 tools. Every tool should do one distinct thing — if a human can't tell which tool to use in a given situation, the AI can't either. Overlapping or vague tools are the #1 cause of wrong tool selection.

### 3. Return Actionable Errors

Use `is_error: true` in tool results when something fails. Include what went wrong and how to fix it — not opaque error codes.

### 4. Don't Require Computed Inputs

Parameters should accept natural values the LLM can provide (text strings, names, descriptions), not computed values (line numbers, byte offsets, hashes).

### 5. Use Strict Mode

Enable `strict: true` on all tool schemas in production. Guarantees the model's output matches the schema exactly.

### 6. Inject Static Context, Fetch Dynamic Context

Static info (time, file listing, profile summary) goes in the system prompt. Dynamic info (weather, web results, file contents) is fetched via tools.

### 7. Encourage Parallel Tool Use

Tell the agent in the system prompt: "When you need multiple independent pieces of information, call all relevant tools simultaneously rather than sequentially."

---

## Injected Context (Not Tools)

Always present in the system prompt — no tool needed:

- **Current time** — `"Current time: 2026-02-21T14:30:00+01:00, Saturday"` injected every call
- **Available files** — listing of all files in the data directory, refreshed when files are created

---

## Automatic Behaviors (Not Tools)

Handled by the agent loop, not via tool calls:

- **Context compaction** — when token count exceeds threshold, older messages are automatically summarized and replaced before the next API call
- **Message queuing** — if multiple messages arrive while the agent is busy, they're batched into the next turn

---

## Timer Behavior

The wakeup timer **persists across message-triggered turns**. If a timer is set for 14:30 and the user sends a message at 14:10, the agent wakes for the message but the 14:30 timer stays active. The timer is only replaced when the agent explicitly calls `set_next_wakeup`.

```text
14:00  Agent sets wakeup for 14:30 (reason: "lunch reminder")
14:10  User sends "hey what's up" → agent wakes, responds
       Timer for 14:30 still active
14:30  Timer fires → agent wakes with "[TIMER] Reason: lunch reminder"
       Agent sets new wakeup for 16:00
```

This prevents casual messages from silently losing scheduled check-ins.

---

## Tools (7 Total)

### 1. send_message

Send a message to the user via Telegram. Use this to deliver plans, reminders, answers, and nudges. Supports text, images, or both (image with text caption). At least one of `text` or `image_url` must be provided. Do NOT use this tool to talk to yourself — only for messages the user should see.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `text` | string | no | Message text. Supports Telegram markdown formatting. Required if no `image_url` is provided. E.g. `"Time to leave for ASML — 25 min bike ride."` |
| `image_url` | string | no | URL of an image to send. If `text` is also provided, it becomes the image caption. Required if no `text` is provided. |

**Returns:** `{ "status": "sent", "timestamp": "2026-02-21T14:30:00" }`

**Error example:** `{ "error": "Telegram API error: chat not found. Verify chat_id in config.", "is_error": true }`

---

### 2. read_file

Read a file from the data directory. Use this to check any file you've created — memory, plans, profile info, or anything else. Check the "Available files" listing in your system prompt to see what files exist. Do NOT guess file paths — only read files that appear in the listing.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | Relative path from the data directory, as shown in the "Available files" listing. E.g. `"memory.md"`, `"groceries.md"`, `"training.md"`. |

**Returns:** `{ "content": "file contents as string" }`

**Error example:** `{ "error": "File not found: profile/wardrobe.md. Check the Available files listing for valid paths.", "is_error": true }`

---

### 3. write_file

Write or overwrite a file in the data directory. Use this to create new files or completely replace an existing file's contents. You decide what files to create and how to organize them. If you want to preserve parts of an existing file, use `read_file` first, then write the full updated version. For small targeted edits to large files, prefer `edit_file` instead. Creates parent directories if they don't exist.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | Relative path from the data directory. E.g. `"groceries.md"`, `"training.md"`. New files will appear in the "Available files" listing after creation. |
| `content` | string | yes | The complete file content to write. This replaces the entire file. |

**Returns:** `{ "status": "written", "path": "plan.md" }`

---

### 4. edit_file

Apply a targeted text replacement in a file. Use this when you need to change a specific section of a large file without rewriting the whole thing — for example, updating one time block in the plan, or adding a line to your memory notes. Do NOT use this if you need to restructure the entire file — use `write_file` for that. The `old_text` must match exactly (including whitespace and newlines).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | Relative path from the data directory. The file must already exist. |
| `old_text` | string | yes | The exact text to find in the file. Must be unique within the file. Copy it exactly as it appears, including whitespace. |
| `new_text` | string | yes | The replacement text. Can be longer, shorter, or empty (to delete the old text). |

**Returns:** `{ "status": "edited", "path": "plan.md" }`

**Error example:** `{ "error": "old_text not found in plan.md. Use read_file to check the current content, then retry with the exact text.", "is_error": true }`

---

### 5. set_next_wakeup

Schedule when you should next wake up on your own, without waiting for a user message. Use this to plan proactive check-ins, reminders, and timed nudges. A timer should always be active so you don't lose track of the user. Only one timer can be active at a time — calling this replaces any existing timer. The timer is NOT cancelled when the user sends a message; it persists until you explicitly replace it or it fires.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `time` | string | yes | When to wake up. Accepts ISO time for today (`"14:30"`), full ISO datetime (`"2026-02-22T07:00:00"`), or relative duration (`"+45m"`, `"+2h"`). |
| `reason` | string | yes | Why you are waking up. This is included verbatim in your prompt when the timer fires, so make it specific and actionable. E.g. `"Check if user has left for ASML"`, `"Remind to start cooking dinner"`. |

**Returns:** `{ "status": "scheduled", "wakeup_at": "2026-02-21T14:30:00", "reason": "Check if user has left for ASML" }`

---

### 6. get_weather

Get current weather conditions and a multi-hour forecast. Use this for clothing recommendations (jacket? umbrella?), commute planning (rain during bike ride?), and activity suggestions (outdoor training feasible?). Do NOT call this more than once per hour unless the user has changed location — weather doesn't change that fast.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `location` | string | no | City name (e.g. `"Eindhoven"`) or coordinates (e.g. `"51.44,5.47"`). Defaults to user's configured home location if omitted. |
| `forecast_hours` | integer | no | Number of hours ahead to include in the forecast. Default: 12. Use a smaller value (e.g. 3) if you only need the next few hours. |

**Returns:**

```json
{
  "current": {
    "temperature_c": 8,
    "feels_like_c": 5,
    "condition": "Partly cloudy",
    "wind_kmh": 15,
    "precipitation_mm": 0,
    "humidity_pct": 72
  },
  "forecast": [
    { "time": "15:00", "temp_c": 9, "condition": "Cloudy", "rain_chance_pct": 30 },
    { "time": "18:00", "temp_c": 6, "condition": "Light rain", "rain_chance_pct": 80 }
  ]
}
```

**Error example:** `{ "error": "Weather API unavailable (HTTP 503). Try again in a few minutes, or skip weather-dependent advice for now.", "is_error": true }`

---

### 7. web_search

Search the web for information you don't have in your files or memory. Use this for recipes, store hours, product prices, transit schedules, event info, or anything else you need from the internet. Do NOT use this for information that's already in your profile files — read those instead. Returns the top 3-5 results with titles, snippets, and URLs.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | yes | The search query. Be specific for better results. E.g. `"quick chicken broccoli rice recipe 30 minutes"` rather than `"dinner recipe"`. |

**Returns:**

```json
{
  "results": [
    {
      "title": "30-Minute Chicken Broccoli Rice Bowl",
      "snippet": "A quick one-pan meal with chicken, broccoli, and rice...",
      "url": "https://example.com/recipe"
    }
  ]
}
```

**Error example:** `{ "error": "Search API rate limit exceeded. Wait 60 seconds before searching again.", "is_error": true }`

---

## Full Tool Registration (Anthropic API Format)

```python
TOOLS = [
    {
        "name": "send_message",
        "description": "Send a message to the user via Telegram. Use this to deliver plans, reminders, answers, and nudges. Supports text, images, or both (image with text caption). At least one of text or image_url must be provided. Do NOT use this to talk to yourself — only for messages the user should see.",
        "input_schema": {
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
        }
    },
    {
        "name": "read_file",
        "description": "Read a file from the data directory. Use to check any file you've created — memory, plans, profile info, etc. Check the 'Available files' listing in your system prompt to see what exists. Do NOT guess paths — only read files from the listing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from the data directory, as shown in 'Available files'. E.g. 'memory.md', 'groceries.md', 'training.md'."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a file in the data directory. You decide what files to create and how to organize them. Replaces the entire file — use read_file first to preserve content, or edit_file for small changes. Also creates new files and parent directories.",
        "input_schema": {
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
        }
    },
    {
        "name": "edit_file",
        "description": "Apply a targeted text replacement in a file. Use this for small changes to large files — e.g. updating one time block in the plan, or adding a line to memory. Do NOT use if you need to restructure the whole file — use write_file for that. old_text must match exactly including whitespace.",
        "input_schema": {
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
        }
    },
    {
        "name": "set_next_wakeup",
        "description": "Schedule when to wake up next without a user message. Use for proactive check-ins, reminders, and timed nudges. A timer should always be active so you don't lose track of the user. Only one timer active at a time — calling this replaces any existing timer. Timer persists when user messages arrive; only replaced by calling this tool again.",
        "input_schema": {
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
        }
    },
    {
        "name": "get_weather",
        "description": "Get current weather and forecast. Use for clothing advice (jacket? umbrella?), commute planning (rain during bike ride?), and activity suggestions. Do NOT call more than once per hour unless location changed.",
        "input_schema": {
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
        }
    },
    {
        "name": "web_search",
        "description": "Search the web for information not in your files. Use for recipes, store hours, prices, transit, events. Do NOT use for info already in profile files — read those instead. Be specific in queries for better results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Be specific: 'quick chicken broccoli rice recipe 30 minutes' not 'dinner recipe'."
                }
            },
            "required": ["query"]
        },
        "cache_control": {"type": "ephemeral"}
    }
]
```
