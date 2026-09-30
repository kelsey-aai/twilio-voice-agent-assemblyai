# Twilio voice agent with AssemblyAI

A runnable inbound phone voice agent that answers Twilio calls, transcribes the caller in real time with AssemblyAI Universal-3.6 Pro Realtime, decides what to say with GPT-4o, and replies through ElevenLabs streaming TTS. It handles keypad (DTMF) input, passes `agent_context` to the speech model, and supports barge-in.

This is the companion repo for [How to build a voice agent with Twilio and AssemblyAI](https://www.assemblyai.com/blog/build-voice-agent-twilio-assemblyai) (Path B, the DIY FastAPI bridge).

> **Want the short path?** If the standard STT + LLM + TTS pipeline is fine for you, skip the bridge: point a Twilio SIP endpoint at the [Voice Agent API](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/connect-to-twilio) and start from [voice-agent-starter-python](https://github.com/AssemblyAI/voice-agent-starter-python) or [voice-agent-starter-js](https://github.com/AssemblyAI/voice-agent-starter-js). The SIP leg runs under 500ms, DTMF is handled for you, and billing is a flat $4.50/hr covering STT, LLM and TTS. Use this repo when you need your own LLM, your own voice, or custom turn logic.

**Stack**

- **Telephony:** Twilio Voice + Media Streams (inbound)
- **Streaming speech-to-text:** AssemblyAI Universal-3.6 Pro Realtime (`speech_model=universal-3-6-pro`, `encoding=pcm_mulaw`, `sample_rate=8000`, `voice_focus=near-field`)
- **LLM + function calling:** OpenAI GPT-4o
- **Text-to-speech:** ElevenLabs (`ulaw_8000` output, no resampling)
- **Server:** FastAPI + WebSockets

**Why this stack works for phone**

Twilio sends 8kHz mulaw audio. Universal-3.6 Pro Realtime accepts `pcm_mulaw` at `sample_rate=8000` natively. ElevenLabs returns `ulaw_8000` directly. The entire audio path stays in mulaw, with zero resampling.

On AssemblyAI's [English voice-agent benchmark](https://www.assemblyai.com/benchmarks) (12,460 scripted voice-agent scenarios, lower is better), Universal-3.6 Pro Realtime posts a 5.19% word error rate and a 14.4% entity error rate, including 2.4% on phone numbers and 10.9% on names. Streaming speech-to-text covers 32 languages.

---

## Architecture

```
  Caller's phone
        │
     Twilio Voice (PSTN)
        │  TwiML → open WebSocket
        ▼
  This server (FastAPI)
   ┌────┴────┐
   ▼         ▲
 AssemblyAI   ElevenLabs TTS
 Universal-   (ulaw_8000)
 3.6 Pro
 Realtime
   │             ▲
   │ transcript  │ audio
   ▼             │
   GPT-4o + tools
   (get_order_status, transfer_to_human)
```

---

## Quickstart

Requires Python 3.11+ and [ngrok](https://ngrok.com).

### 1. Clone and install dependencies

```bash
git clone https://github.com/kelsey-aai/twilio-voice-agent-assemblyai.git
cd twilio-voice-agent-assemblyai
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

You'll need:

- `ASSEMBLYAI_API_KEY` — from [assemblyai.com/dashboard](https://www.assemblyai.com/dashboard/signup)
- `OPENAI_API_KEY` — from platform.openai.com
- `ELEVENLABS_API_KEY` — from elevenlabs.io
- `ELEVEN_VOICE_ID` — optional, defaults to a clear neutral voice (the older `ELEVENLABS_VOICE_ID` name is still read)

### 3. Run the server

```bash
uvicorn server:app --port 8000
```

### 4. Expose with ngrok

In a second terminal:

```bash
ngrok http 8000
```

Copy the HTTPS URL ngrok prints.

### 5. Point a Twilio number at the server

In the [Twilio console](https://console.twilio.com), pick a Voice-enabled number. Under **A call comes in**, set the webhook to:

```
https://<your-ngrok-host>/twilio/voice
```

with method `POST`. Save.

### 6. Call it

Call the Twilio number from your phone. The agent greets you, transcribes what you say, looks up orders, and offers to transfer you to a human if you ask. Try typing an order ID on the keypad followed by `#` (demo IDs: `AB3792`, `CD1204`, `EF5566`; note that letters can't be typed on a keypad, so keypad entry is best for numeric IDs in your real system).

---

## What's in each file

| File | Purpose |
|---|---|
| `server.py` | FastAPI app with the TwiML endpoint and `/media-stream` WebSocket bridge. Pumps audio Twilio → AssemblyAI and TTS → Twilio, handles DTMF, sends `agent_context`, and clears Twilio playback on barge-in. |
| `tools.py` | Tool definitions (`get_order_status`, `transfer_to_human`) and dispatcher. Replace stubs with real CRM / order-system calls. |
| `requirements.txt` | Python deps. |
| `.env.example` | Required environment variables. |

---

## Streaming config notes

- **`speech_model=universal-3-6-pro`** — streaming takes the singular `speech_model`; the async API takes a plural `speech_models` list.
- **No `format_turns`** — it's a legacy parameter with no effect on the U3 Pro family. Turns always come back formatted, punctuated and cased.
- **`voice_focus=near-field`** — isolates the primary speaker and suppresses background noise. `near-field` is right for headsets and phone handsets; `far-field` is for rooms, kiosks and drive-thrus.
- **`agent_context`** — before the agent speaks, `server.py` sends `{"type": "UpdateConfiguration", "agent_context": "<what the agent is about to say>"}` so the model knows what kind of answer to expect (a zip code, an order ID, a yes/no). This helps most on short, entity-heavy replies.
- **End-of-turn detection** combines semantic context with voice activity rather than a silence timer, so the server acts on `end_of_turn` directly.
- **Barge-in** — when a new `Turn` arrives while the agent is speaking, the server cancels TTS and sends Twilio a `clear` event to flush its playback buffer.
- **DTMF** — digits are buffered, `#` submits them to the conversation as `[caller entered: ...]`, `*` clears the buffer.

---

## Latency notes

Phone calls are unforgiving about silence. On a DIY bridge, your end-to-end latency is the sum of your own LLM and TTS choices. The four things that reliably wreck the budget:

- **Resampling** — keep audio in mulaw end-to-end (`encoding=pcm_mulaw` in, `output_format=ulaw_8000` out)
- **Non-streaming LLM calls** — waiting for a complete response before starting TTS creates a dead zone the length of the whole generation
- **Slow tool calls** — a lookup that takes 800ms is 800ms of dead air unless you cover it; acknowledge first, look up second
- **Serialized TTS** — `eleven.text_to_speech.stream` sends chunks as they're generated; don't buffer the full reply

---

## Cost

- AssemblyAI Streaming Speech-to-Text (Universal-3.6 Pro Realtime): **$0.45/hr** base. Streaming rate limits scale with your usage; check your account's concurrency headroom.
- OpenAI GPT-4o: pay-per-token
- ElevenLabs streaming TTS: per-character or per-minute depending on plan
- Twilio voice: per-minute, varies by country

For comparison, the managed [Voice Agent API](https://www.assemblyai.com/pricing) is a flat $4.50/hr ($0.075/min) covering STT, LLM and TTS, plus Twilio.

---

## Going to production

- Replace the in-memory per-call `CallSession` with a session store (Redis, Postgres, or a managed session service) if you need calls to survive a restart
- Add PII redaction on transcripts before they hit your CRM/logs
- Set up call recording with proper consent flows (two-party consent states require disclosure)
- Add timeout and retry logic on tool calls — never let a slow database call eat the turn
- Implement graceful handoff to a human agent with full conversation context attached

---

## License

MIT. Fork it, ship it.
