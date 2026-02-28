# AIBoss — Agent Loop Architecture

> How the agent runs from startup to shutdown, step by step.

## Overview

The agent is a single Python process with two concurrent tasks:

1. **Telegram listener** — receives your messages, puts them in a queue
2. **Agent loop** — waits for a trigger (message or timer), calls the API, executes tools, repeats

```text
┌─────────────────────────────────────────────────────┐
│                   Python Process                     │
│                                                      │
│  ┌──────────────┐          ┌──────────────────────┐  │
│  │   Telegram    │  queue   │     Agent Loop        │  │
│  │   Listener    │────────>│                        │  │
│  │              │          │  wait for trigger      │  │
│  │  (webhook or │          │  build prompt          │  │
│  │   polling)   │          │  call Anthropic API    │  │
│  └──────────────┘          │  execute tools         │  │
│                            │  repeat until done     │  │
│         ┌──────────────────│  set next wakeup       │  │
│         │  timer           │  go back to waiting    │  │
│         │                  └──────────────────────┘  │
│         v                                            │
│  ┌──────────────┐          ┌──────────────────────┐  │
│  │  Wakeup Timer │          │    State Files        │  │
│  │  (asyncio)   │          │  plan.md, memory.md   │  │
│  └──────────────┘          │  profile/, history     │  │
│                            └──────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## Startup

```text
1. Load config (API keys, Telegram bot token, chat ID, timezone, data directory)
2. Load conversation history from disk (history.json)
3. Initialize Telegram bot (start polling or webhook)
4. Initialize wakeup timer (set to now → triggers first run immediately)
5. Enter main loop
```

No day-based resets. The conversation runs continuously across days. Context compaction handles length naturally — sleep and wake-up are just scheduled events like work or meals.

---

## Main Loop

```python
async def main_loop():
    while True:
        # === WAIT FOR TRIGGER ===
        trigger = await wait_for_either(
            message_queue.get(),      # user sent a Telegram message
            wakeup_timer.wait()       # scheduled timer expired
        )

        # === DETERMINE TRIGGER TYPE ===
        # Timer is NOT auto-cancelled. It persists until the agent
        # explicitly replaces it by calling set_next_wakeup, or it fires.
        if trigger is a message:
            trigger_content = format_user_message(trigger)
            # could be text, image, voice, location
        elif trigger is a timer:
            trigger_content = f"[TIMER] Wakeup at {now}. Reason: {timer.reason}"

        # === DRAIN MESSAGE QUEUE ===
        # If multiple messages came in while we were busy, grab them all
        additional_messages = drain_queue(message_queue)
        trigger_content += additional_messages

        # === RUN AGENT TURN ===
        await run_agent_turn(trigger_content)
```

---

## Agent Turn (The Core)

This is where the LLM is called and tools are executed in a loop.

```python
async def run_agent_turn(trigger_content):
    # 1. Add trigger to conversation history
    conversation.append({"role": "user", "content": trigger_content})

    # 2. Build the API request
    system_prompt = build_system_prompt()  # see below

    # 3. Agentic tool loop
    while True:
        response = anthropic.messages.create(
            model="claude-sonnet-4-6-20250514",
            max_tokens=4096,
            system=system_prompt,
            messages=conversation,
            tools=TOOL_DEFINITIONS,      # last tool has cache_control
        )

        # 4. Add assistant response to history
        conversation.append({"role": "assistant", "content": response.content})

        # 5. Check if the model wants to use tools
        tool_calls = [block for block in response.content if block.type == "tool_use"]

        if not tool_calls:
            # Agent wants to end its turn — check if a timer is active
            if not wakeup_timer.is_active():
                conversation.append({
                    "role": "user",
                    "content": "[SYSTEM] Heads up — there's no wakeup timer set. When should you check in next?"
                })
                continue  # loop back for another API call
            # Timer is active → turn is done
            break

        # 6. Execute each tool and collect results
        tool_results = []
        for tool_call in tool_calls:
            result = await execute_tool(tool_call.name, tool_call.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": json.dumps(result)
            })

        # 7. Feed tool results back as a user message
        conversation.append({"role": "user", "content": tool_results})

        # 8. Loop back → the model sees the tool results and decides:
        #    - Call more tools?
        #    - Send a message to the user?
        #    - Do nothing?
        #    The loop continues until the model responds with no tool calls.

    # 9. Auto-compact if context is getting long
    if estimate_tokens(conversation) > TOKEN_THRESHOLD:
        await compact_conversation(conversation)

    # 10. Save conversation and logs to disk
    save_history(conversation)
```

### Logging

Two log files, appended to after every agent turn:

**`logs/simple.log`** — human-readable, just the conversation:

```text
[2026-02-22 14:30:00] TIMER: Afternoon check-in
[2026-02-22 14:30:02] TOOL: read_file("plan.md")
[2026-02-22 14:30:02] TOOL: get_weather()
[2026-02-22 14:30:03] TOOL: send_message("Rain in 2 hours, bring a jacket when you leave")
[2026-02-22 14:30:03] TOOL: set_next_wakeup("16:30", "Check if user left work")
[2026-02-22 14:35:12] USER: "thanks, will do"
[2026-02-22 14:35:13] TOOL: send_message("👍")
```

**`logs/debug.log`** — full technical detail per API call:

```text
[2026-02-22 14:30:01] API CALL #1
  trigger: timer (Afternoon check-in)
  model: claude-sonnet-4-6-20250514
  input_tokens: 1847
  cache_read_input_tokens: 1203
  cache_creation_input_tokens: 0
  output_tokens: 312
  thinking_tokens: 89
  tool_calls: [read_file("plan.md"), get_weather()]
  latency_ms: 1230
  cost_estimate: $0.0082

[2026-02-22 14:30:02] API CALL #2
  input_tokens: 2405
  cache_read_input_tokens: 1203
  output_tokens: 187
  thinking_tokens: 45
  tool_calls: [send_message("Rain in 2 hours..."), set_next_wakeup("16:30", "...")]
  latency_ms: 890
  cost_estimate: $0.0054

[2026-02-22 14:30:03] TURN COMPLETE
  total_api_calls: 2
  total_input_tokens: 4252
  total_output_tokens: 499
  total_cost_estimate: $0.0136
  conversation_length: 47 messages
  active_timer: 16:30 (Check if user left work)
```

Token counts come directly from the API response's `usage` object — no estimation needed.

### What Happens in a Typical Turn

Example: Timer fires at 07:00, reason "Morning planning"

```text
Turn starts:
  trigger = "[TIMER] Wakeup at 07:00. Reason: Morning planning"

API call 1:
  Model thinks: "It's morning, let me check the plan and weather"
  → calls read_file("plan.md")
  → calls get_weather()

  Results fed back:
  → plan.md: (empty, new day)
  → weather: 6°C, rain expected at 14:00

API call 2:
  Model thinks: "Let me check what food is at home for breakfast"
  → calls read_file("profile/food_at_home.csv")
  → calls read_file("profile/training.md")

  Results fed back:
  → food: eggs, bread, milk, ...
  → training: push day, goal is 5x/week

API call 3:
  Model thinks: "I have everything I need. Create the plan, send morning message, set next wakeup"
  → calls write_file("plan.md", "07:00 - Training (push)...")
  → calls send_message("Good morning. Here's your plan:...")
  → calls set_next_wakeup("08:15", "Check if training is done")

  No more tool calls → turn ends.
```

---

## System Prompt Construction

Built fresh each call, but the content is stable so prompt caching kicks in.

```python
def build_system_prompt():
    current_time = datetime.now(tz).isoformat()

    return [
        {
            # Block 1: Role + behavior (CACHED — never changes)
            "type": "text",
            "text": ROLE_AND_BEHAVIOR_PROMPT,
            "cache_control": {"type": "ephemeral"}
        },
        {
            # Block 2: Available files + current time (NOT cached — changes across calls)
            "type": "text",
            "text": f"Available files:\n{file_listing}\n\nCurrent time: {current_time}"
        }
    ]
```

Cache breakpoints: on the last tool definition + on system block 1. Block 2 (files + time) changes freely without breaking the cache. Tools + block 1 together exceed Sonnet 4.6's 1024-token minimum for caching.

---

## Tool Execution

```python
async def execute_tool(name: str, args: dict) -> dict:
    match name:
        case "send_message":
            text = args.get("text")
            image_url = args.get("image_url")
            if image_url:
                await telegram_bot.send_photo(
                    chat_id=CONFIG.chat_id,
                    photo=image_url,
                    caption=text
                )
            else:
                await telegram_bot.send_message(
                    chat_id=CONFIG.chat_id,
                    text=text,
                    parse_mode="Markdown"
                )
            return {"status": "sent", "timestamp": now_iso()}

        case "read_file":
            path = sanitize_path(args["path"])  # prevent directory traversal
            content = read_text_file(DATA_DIR / path)
            return {"content": content}

        case "write_file":
            path = sanitize_path(args["path"])
            (DATA_DIR / path).parent.mkdir(parents=True, exist_ok=True)
            write_text_file(DATA_DIR / path, args["content"])
            refresh_file_listing()  # update the list in the system prompt
            return {"status": "written", "path": args["path"]}

        case "edit_file":
            path = sanitize_path(args["path"])
            content = read_text_file(DATA_DIR / path)
            if args["old_text"] not in content:
                return {"error": f"old_text not found in {args['path']}. Use read_file to check current content.", "is_error": True}
            content = content.replace(args["old_text"], args["new_text"], 1)
            write_text_file(DATA_DIR / path, content)
            return {"status": "edited", "path": args["path"]}

        case "set_next_wakeup":
            time = parse_wakeup_time(args["time"])  # handles "14:30", "+45m", ISO
            reason = args["reason"]
            wakeup_timer.schedule(time, reason)
            return {"status": "scheduled", "wakeup_at": time.isoformat(), "reason": reason}

        case "get_weather":
            location = args.get("location", CONFIG.default_location)
            hours = args.get("forecast_hours", 12)
            weather = await fetch_weather(location, hours)  # calls Open-Meteo API
            return weather

        case "web_search":
            results = await search_web(args["query"])  # calls Tavily API
            return {"results": results[:5]}
```

---

## Context Compaction

When the conversation gets long (~100K tokens), older messages are summarized.

```python
async def compact_conversation(conversation, keep_last_n=10):
    # Split: old messages to summarize, recent messages to keep
    old_messages = conversation[:-keep_last_n]
    recent_messages = conversation[-keep_last_n:]

    # Ask the model to summarize (separate, cheap API call)
    summary_response = anthropic.messages.create(
        model="claude-sonnet-4-6-20250514",
        max_tokens=2000,
        system="Summarize this conversation into key facts, decisions, and current state. Be concise.",
        messages=[{"role": "user", "content": format_messages_as_text(old_messages)}]
    )

    summary = summary_response.content[0].text

    # Replace conversation with: summary + recent messages
    conversation.clear()
    conversation.append({
        "role": "user",
        "content": f"[CONTEXT SUMMARY]\n{summary}"
    })
    conversation.append({
        "role": "assistant",
        "content": "Understood. I have the context from the summary and will continue from here."
    })
    conversation.extend(recent_messages)

    save_history(conversation)
```

---

## Telegram Listener

```python
from telegram.ext import ApplicationBuilder, MessageHandler, filters

async def handle_telegram_message(update, context):
    message = update.message

    # Build a rich trigger from the Telegram message
    content = []

    if message.text:
        content.append({"type": "text", "text": message.text})

    if message.photo:
        # Download the image, convert to base64 for the API
        photo_file = await message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        content.append({
            "type": "image",
            "source": {"type": "base64", "data": base64.b64encode(image_bytes).decode()}
        })

    if message.location:
        content.append({
            "type": "text",
            "text": f"[LOCATION] lat={message.location.latitude}, lon={message.location.longitude}"
        })

    if message.voice:
        # Could transcribe with Whisper or just note that voice was sent
        content.append({"type": "text", "text": "[VOICE MESSAGE - transcription not yet supported]"})

    # Push to queue → agent loop picks it up immediately
    await message_queue.put(content)

# Setup
app = ApplicationBuilder().token(CONFIG.telegram_token).build()
app.add_handler(MessageHandler(filters.ALL, handle_telegram_message))
```

---

## Wakeup Timer

```python
class WakeupTimer:
    def __init__(self):
        self._event = asyncio.Event()
        self._wakeup_time = None
        self._reason = ""

    def is_active(self) -> bool:
        return self._wakeup_time is not None

    def schedule(self, time: datetime, reason: str):
        """Cancel existing timer and schedule a new one."""
        self._wakeup_time = time
        self._reason = reason
        self._event.set()  # unblock the wait if currently sleeping

    async def wait(self) -> str:
        """Sleep until the scheduled time. Returns the reason."""
        while True:
            self._event.clear()
            if self._wakeup_time is None:
                # No timer set — wait indefinitely until schedule() is called
                await self._event.wait()
                continue

            delay = (self._wakeup_time - datetime.now(tz)).total_seconds()
            if delay <= 0:
                reason = self._reason
                self._wakeup_time = None
                return reason

            try:
                await asyncio.wait_for(self._event.wait(), timeout=delay)
                # event was set → timer was rescheduled, loop again
            except asyncio.TimeoutError:
                # Timer expired naturally
                reason = self._reason
                self._wakeup_time = None
                return reason
```

---

## Conversation Persistence

```python
HISTORY_FILE = DATA_DIR / "history.json"

def save_history(conversation):
    """Save after every turn. No day-based resets — runs continuously."""
    with open(HISTORY_FILE, "w") as f:
        json.dump({"messages": conversation}, f, indent=2)

def load_history() -> list:
    """Load on startup. Picks up where it left off."""
    if not HISTORY_FILE.exists():
        return []
    return json.load(open(HISTORY_FILE))["messages"]
```

---

## Putting It All Together

```python
import asyncio

async def main():
    # 1. Load config and state
    config = load_config()
    conversation = load_history()
    timer = WakeupTimer()
    message_queue = asyncio.Queue()

    # 2. Start Telegram listener
    telegram_app = setup_telegram(config, message_queue)

    # 3. Schedule first wakeup (now → triggers immediately)
    timer.schedule(datetime.now(tz), "Startup — plan the day or resume")

    # 4. Main loop
    async def agent_loop():
        while True:
            # Wait for either a message or the timer
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(message_queue.get()),
                    asyncio.create_task(timer.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel the task that didn't finish
            for task in pending:
                task.cancel()

            # Get the trigger
            result = done.pop().result()

            # Drain any additional queued messages
            while not message_queue.empty():
                extra = message_queue.get_nowait()
                # append to result

            # Run the agent turn
            await run_agent_turn(result, conversation, timer, config)

    # 5. Run both concurrently
    await asyncio.gather(
        telegram_app.run_polling(),
        agent_loop()
    )

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Lifecycle of a Typical Day

```text
07:00  Timer fires (startup or morning wakeup)
       → Agent reads profile, weather, creates plan
       → Sends "Good morning" message with plan
       → Sets wakeup for 08:15

08:15  Timer fires (reason: "Check if training is done")
       → Sends "How was training?"
       → Sets wakeup for 08:45

08:20  You reply "Done, it was tough"
       → Agent updates memory, adjusts plan if needed
       → "Good work. Eat breakfast, leave for ASML by 09:10. It'll rain at 14:00, bring a jacket."

09:05  Timer fires (reason: "Remind to leave for ASML")
       → "Leave now — 25 min bike ride."

12:00  Timer fires (reason: "Lunch reminder")
       → Reads food_at_home.csv
       → "You have leftover pasta at home but you're at work. Buy a sandwich, you have €8 left in today's budget."

14:30  You send a photo of your desk
       → Agent sees the image, understands context
       → "Looks like you're deep in work. I'll check in at 16:00."

16:00  Timer fires (reason: "Afternoon check-in")
       → "2 hours left at work. Meeting at 16:30?"
       → You reply "cancelled"
       → "Nice, that frees up time. Leave at 17:00, you can do a longer grocery run."

17:00  Timer fires
       → "Time to head home. Pick up: chicken, rice, broccoli (€6.50 estimate, within budget)."

...and so on until bedtime.
```
