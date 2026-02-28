# Voice Integration — Research & Design Notes

> Status: Research complete, not yet implemented.

## TL;DR

Use **Telegram voice messages** (async, not real-time calls) with the **Gemini Live API's native audio** mode. Since we already use Gemini via Vertex AI, this eliminates the need for separate STT/TTS services entirely. User sends a voice clip, bot replies with a voice clip. Simple.

## 1. How Telegram Voice Works

- Telegram voice messages are **OGG files encoded with Opus** (`audio/ogg`).
- `python-telegram-bot` has built-in support:
  - **Receive**: `MessageHandler(filters.VOICE, handler)` — download via `voice.get_file()`
  - **Send**: `bot.send_voice(chat_id, ogg_file)` — must be OGG/Opus format
- Telegram **does not support bot-initiated voice/video calls**. Real-time calling would require external infrastructure (Twilio, WebRTC). Overkill for our use case.

## 2. Architecture Options

### Option A: Async Voice Messages via Gemini Live API (Recommended)

```
User sends voice clip (OGG/Opus)
  → python-telegram-bot downloads OGG
  → FFmpeg converts OGG → 16kHz PCM
  → Gemini 2.5 Flash Native Audio (Vertex AI Live API)
  → Returns 24kHz PCM audio + text transcript
  → FFmpeg converts PCM → OGG/Opus
  → Bot sends voice reply + optional text caption
```

**Why this wins:**
- Already on Vertex AI — no new vendor
- Native audio = no separate STT/TTS pipeline needed
- Latency: 320-800ms for first audio response (fine for async messages)
- Cost: ~$0.0015/minute of audio (extremely cheap)
- Simple: just OGG↔PCM conversion via FFmpeg

### Option B: Cascading STT → LLM → TTS Pipeline

```
User sends voice clip (OGG)
  → FFmpeg converts to WAV
  → Deepgram/Whisper transcribes to text
  → Gemini generates text response (existing flow)
  → ElevenLabs/Cartesia converts text to speech
  → FFmpeg converts to OGG
  → Bot sends voice reply
```

**When to consider this instead:**
- If you want best-in-class voice quality (ElevenLabs sounds more natural than Gemini native audio)
- If you want text transcripts for debugging/logging at each step
- If Gemini Live API has issues with tool calling (native audio may have limitations vs text mode)

### Option C: Real-Time Voice Calls (Future / Overkill)

Would require Twilio, LiveKit, or Pipecat for WebRTC infrastructure. Not worth it for a personal Telegram bot.

## 3. Key Technology Comparison

### Native Audio (Speech-to-Speech)
| | Gemini Live API | OpenAI Realtime API |
|---|---|---|
| Latency | 320-800ms | 220-400ms |
| Pricing | ~$0.0015/min | ~$0.06/min (40x more) |
| Approach | WebSocket, native audio | WebSocket or WebRTC |
| Integration | Already on Vertex AI | New vendor |

### STT Services (if cascading)
| Service | Latency | Accuracy | Cost/1000 min |
|---------|---------|----------|---------------|
| Deepgram Nova-3 | <300ms | Best (6.84% WER) | $4.30 |
| OpenAI gpt-4o-transcribe | Moderate | Excellent | $6.00 |
| Google Cloud STT | ~300ms | Good | $16.00 |
| Whisper (self-hosted) | Varies | Good | Free |

### TTS Services (if cascading)
| Service | TTFB | Quality | Cost |
|---------|------|---------|------|
| ElevenLabs Flash v2.5 | ~75ms | Best naturalness | ~$0.30/1K chars |
| Cartesia Sonic | ~95ms | Very good | $0.011/1K chars |
| Deepgram Aura-2 | ~90ms | Good | Budget |
| Google Cloud TTS | ~200ms | Lower quality | Moderate |

### Voice Agent Platforms (if real-time needed later)
| Platform | Type | Cost | Notes |
|----------|------|------|-------|
| Pipecat | Open-source framework | Free + infra | By Daily.co, Python native, 40+ plugins |
| LiveKit | Open-source framework | Free tier available | Best for self-hosted |
| Vapi | Managed platform | $0.13-0.33/min | Easiest setup, telephony-focused |
| Deepgram Voice Agent API | Managed | $4.50/hr | Full stack in one API |

## 4. Implementation Plan

### Phase 1: Voice message input (text response)
- Add `MessageHandler(filters.VOICE, handle_voice)` to `telegram_handler.py`
- Download OGG, convert to PCM with FFmpeg
- Send PCM audio to Gemini (can use existing text model with audio input)
- Bot replies with text (same as current text flow)
- **This is the simplest starting point — voice in, text out**

### Phase 2: Voice message output
- After getting Gemini's text response, convert to speech
- Option A: Use Gemini Live API native audio for the response
- Option B: Use a TTS service (ElevenLabs/Cartesia)
- Convert output to OGG/Opus, send via `bot.send_voice()`
- Send text transcript alongside as caption

### Phase 3: Full native audio (if Phase 1-2 work well)
- Switch to Gemini Live API for end-to-end native audio
- Audio in → audio out, with text transcripts as side channel
- Uses `client.aio.live.connect()` from the Google Gen AI SDK

## 5. Dependencies

- **FFmpeg** — required for OGG↔PCM conversion. Install via `brew install ffmpeg` or add to Docker image.
- **google-genai** SDK — already used, but needs Live API support (`client.aio.live.connect()`)
- No new Python packages needed for Phase 1 (python-telegram-bot already handles voice)

## 6. Open Questions

- Does Gemini Live API support tool calling in native audio mode? If not, voice interactions might not be able to trigger tools (set timers, read files, etc.) — would need to fall back to text mode for those.
- Should voice replies be the default, or should the bot mirror the input format? (voice in → voice out, text in → text out)
- Audio quality: is Gemini's native audio voice quality good enough, or would ElevenLabs be worth the extra cost/complexity?

## Sources

- [Gemini Live API overview (Vertex AI)](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api)
- [Gemini Live API — Get started with SDK](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api/get-started-sdk)
- [Gemini Native Audio on Vertex AI](https://cloud.google.com/blog/topics/developers-practitioners/how-to-use-gemini-live-api-native-audio-in-vertex-ai)
- [Vertex AI Pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing)
- [python-telegram-bot Voice docs](https://docs.python-telegram-bot.org/telegram.voice.html)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Voice Agent Architecture Comparison (Softcery)](https://softcery.com/lab/ai-voice-agents-real-time-vs-turn-based-tts-stt-architecture)
- [STT/TTS Comparison Guide (Softcery)](https://softcery.com/lab/how-to-choose-stt-tts-for-ai-voice-agents-in-2025-a-comprehensive-guide)
- [Pipecat Framework](https://github.com/pipecat-ai/pipecat)
- [Deepgram Voice Agent API](https://deepgram.com/product/voice-agent-api)
- [ElevenLabs Conversational AI](https://elevenlabs.io/conversational-ai)
