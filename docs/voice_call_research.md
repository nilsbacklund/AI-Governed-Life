# Voice Call Research (March 2026)

> Status: Research complete, not yet implemented. Paused in favor of other priorities.

## Goal

Real-time voice call experience — press a button, talk, hear the AI respond live. Not async voice messages.

## Options

| Approach | Latency | Cost/month | Complexity | Tool Calling |
|---|---|---|---|---|
| **FastRTC + Gemini Live** | ~500ms-1s | ~$0 | Low | Yes |
| **LiveKit Agents + Gemini Live** | ~500ms-1s | ~$0-5 (self-host) | Medium | Yes |
| **Twilio ConversationRelay** | ~1-2s | ~$10 | Low-Medium | Yes |
| Pipecat + Daily | ~500ms-1s | ~$0-5 | Medium | Yes |
| Telegram voice calls (pytgcalls) | Variable | ~$0 | High | Yes |
| Vapi | ~1-2s | ~$30+ | Low | Yes |

## Key Architectural Decision: LiteLLM vs Gemini Live API

The Gemini Live API is a **separate WebSocket-based speech-to-speech protocol** — LiteLLM doesn't support it. Two approaches:

### Option A: Gemini Live API (speech-to-speech)
- Bypasses LiteLLM, talks to Gemini Live API directly
- **Does NOT share conversation history** with the text agent
- Needs summary injection after call ends
- Lower latency (~500ms-1s)

### Option B: STT → existing agent (LiteLLM) → TTS
- Uses existing agent with full history continuity
- Higher latency (~1.5-2s) due to STT→LLM→TTS cascade
- No new API integration needed — just add STT and TTS

### Option C: Hybrid
- Voice call uses Gemini Live API for real-time interaction
- Tool calls during the call route through existing `execute_tool()`
- After call ends, summary injected into main conversation history

## Recommended Path

**Phase 1 (prototype):** FastRTC + Gemini Live API → browser page with "call" button, ~100 lines of Python

**Phase 2 (production):** LiveKit Agents + Gemini Live plugin → adds phone number support (SIP/Twilio), mobile SDKs, better reliability

## Technology Details

### Gemini Live API
- WebSocket-based, bidirectional audio streaming
- Speech-to-speech (no separate STT/TTS needed)
- Supports tool/function calling mid-conversation
- 16kHz PCM in, 24kHz PCM out
- Cost: ~$0.03 per 10-minute call
- Latency: 500ms-3s (variable, Google still improving)
- Model: `gemini-2.5-flash-native-audio` (on Vertex AI)

### FastRTC (by Hugging Face/Gradio)
- Python library, turns any function into real-time audio stream
- Built-in VAD, turn-taking, Gradio UI
- Free temporary phone number for testing
- Minimal code to get working

### LiveKit Agents
- Open-source WebRTC infrastructure
- Python agent framework with STT/LLM/TTS pipeline
- First-class Gemini Live plugin
- Telephony/SIP integration (add a real phone number)
- iOS/Android/browser SDKs
- Self-hostable, 10K free minutes/month on cloud
- Best production option

### Twilio ConversationRelay
- Real phone number, call from any phone
- WebSocket-based: Twilio does STT/TTS, you handle LLM
- Has a [LiteLLM + Python tutorial](https://www.twilio.com/en-us/blog/developers/tutorials/product/voice-ai-assistant-conversationrelay-litellm-python)
- $0.07/min + phone costs
- Higher latency (~1-2s)

## Sources

- [LiveKit Agents Docs](https://docs.livekit.io/agents/)
- [LiveKit Gemini Live Plugin](https://docs.livekit.io/agents/models/realtime/plugins/gemini/)
- [Gemini Live API (Vertex AI)](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api)
- [Gemini Live API Tool Calling](https://ai.google.dev/gemini-api/docs/live-tools)
- [FastRTC](https://fastrtc.org/)
- [Pipecat](https://github.com/pipecat-ai/pipecat)
- [Twilio ConversationRelay + LiteLLM](https://www.twilio.com/en-us/blog/developers/tutorials/product/voice-ai-assistant-conversationrelay-litellm-python)
