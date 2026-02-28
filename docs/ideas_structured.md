# AIBoss — Structured Ideas

> An LLM agent that manages your day. You don't control it — it controls you.

## Architecture

```text
[You on Telegram] <—> [Telegram Bot API] <—> [Python Agent] <—> [Anthropic API (Sonnet 4.6)]
                                                    |
                                              [State Files]
                                         plan.md, memory.md, profile/
```

### Stack

- **Python + `python-telegram-bot`** — handles messages, runs the agent loop
- **Anthropic SDK** — direct API access with prompt caching (cached input tokens are 90% cheaper). System prompt + profile gets cached across every call = major savings. Can add LiteLLM later for multi-provider support.
- **Telegram Bot** — free, bot has its own identity, supports images/voice/location natively
- **Local files** — plan, memory, profile details, conversation history
- **Run on your Mac** to start (move to a cheap VPS later if needed for reliability)

### Why Telegram over WhatsApp

- Free (Twilio charges per message)
- Bot has its own identity — no confusion about who wrote what
- 2-minute setup via BotFather
- Native support for images, voice, location
- Great Python library

### Why Sonnet 4.6

- Fast responses (important when you're waiting for a reply)
- Cheap (~$3/M input, $15/M output tokens) — dozens of calls per day costs pennies
- Excellent at tool use and following complex instructions
- Swappable: changing model is one string change, no extra cost to your API key

---

## Agent Loop (Sleep + Wake, Not Ticks)

Instead of polling on a fixed timer, the AI controls its own schedule:

```text
while True:
    wait for:
      - new Telegram message from you  → wake immediately
      - OR sleep timer expires          → wake on schedule

    on wake:
      1. Inject current time into prompt
      2. Feed: system prompt + conversation history + trigger (message or "timer fired")
      3. AI reasons, uses tools, responds
      4. AI calls set_next_wakeup("14:30") to schedule its next self-wake
      5. Loop
```

- **No fixed ticks** — the AI decides when to check in next ("wake me in 45 min", "wake me at 14:00")
- **Messages wake it instantly** — you never wait for a tick
- **Fewer API calls** — only runs when there's a reason to
- **Feels like a real conversation**, not a cron job

### Context Management

The conversation runs all day without restarting. When context gets long:

1. AI summarizes older messages into a short recap
2. Old messages are replaced with the summary
3. Recent messages + system prompt stay intact
4. Important state is always in files (plan, memory), so nothing is lost

---

## System Prompt vs Files

### In the System Prompt (always loaded, kept lean)

- Role: "You are my boss. You manage my day."
- Current time (injected every call)
- Available files listing (so the agent knows what it has)
- Available tools list
- Behavioral instructions (tone, when to nudge, how strict to be)

### In Files (agent-managed, free-form)

The agent creates and organizes files however it wants. No prescribed structure. Starts with just `memory.md` (empty). Over time it might create `groceries.md`, `training.md`, `work.md`, `wardrobe.md`, etc. — or it might organize things differently. Its call.

The file listing in the system prompt keeps it oriented. It reads files when it needs detail, keeping the active prompt lean.

---

## What the AI Knows About You

### Learned Through Conversation (not pre-filled)

The agent starts knowing nothing. It asks questions and learns over time:

- Work schedule, commute, meetings
- Training goals and routine
- Sleep preferences
- Food at home, dietary preferences
- Wardrobe
- Budget
- How you feel, what you're doing
- Anything else it needs

It stores what it learns in files it creates and manages itself.

### Always Available

- **Images** — you send photos for context (food, location, situation)
- **Weather** — agent fetches via tool
- **Time** — always injected into the system prompt
- **Your messages** — agent always responds

---

## Core Features

1. **Daily Planning** — generates a plan each morning, covers work, training, meals, commute, sleep
2. **Adaptive Replanning** — replans the rest of the day when something changes
3. **Proactive Messages** — "leave for ASML now", "time to eat", "go to bed"
4. **Always Responsive** — you message it, it always answers
5. **Meal Planning** — what to eat from what's at home, what to buy within budget
6. **Commute Awareness** — bike time, weather impact, "leave now" alerts
7. **Clothing Advice** — what to wear based on weather, schedule, wardrobe
8. **Budget Enforcement** — daily limit, suggests alternatives when tight
9. **Mood-Adaptive** — asks how you feel, adjusts the plan accordingly
10. **Image Context** — you send photos, it understands and adapts

---

## AI Tools (7 total)

- `send_message(text, image_url)` — send a Telegram message (text, image, or both)
- `read_file(path)` — read a state/profile file (file listing always in system prompt)
- `write_file(path, content)` — create or overwrite entire files
- `edit_file(path, old_text, new_text)` — targeted find-and-replace for small edits to large files
- `set_next_wakeup(time, reason)` — schedule next wake (timer persists across message-triggered turns)
- `get_weather(location)` — fetch current weather and forecast
- `web_search(query)` — search the web for info

Time is always injected into the system prompt — no tool needed. Context compaction is automatic — no tool needed.

---

## Technical Details

- **Model**: Sonnet 4.6 via Anthropic SDK with prompt caching (add LiteLLM later for multi-provider)
- **Search**: Tavily (1,000 free credits/month, no card, built for LLM agents)
- **Wake triggers**: incoming message (instant) or scheduled timer (AI-controlled, always active)
- **State files**: `memory.md`, `plan.md`, `profile/` directory
- **Conversation**: runs continuously, no day-based resets. Context compaction handles length.
- **Cost estimate**: ~30-80 API calls/day at short context = ~$1-3/day
- **Privacy**: all state stored locally, only prompt content sent to Anthropic API
- **Start on Mac**: move to VPS ($5/mo) if uptime matters
