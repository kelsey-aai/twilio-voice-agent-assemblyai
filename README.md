# Twilio voice agent with AssemblyAI

A runnable inbound phone-based voice agent that answers Twilio calls, transcribes the caller in real time with AssemblyAI Universal-3 Pro Streaming, decides what to say with GPT-4o, and replies through ElevenLabs streaming TTS — all under an 800ms turn budget.

**Stack**

- **Telephony:** Twilio Voice + Media Streams (inbound)
- **Streaming speech-to-text:** AssemblyAI Universal-3 Pro Streaming (`speech_model=u3-rt-pro`, `encoding=pcm_mulaw`, `sample_rate=8000`)
- **LLM + function calling:** OpenAI GPT-4o
- **Text-to-speech:** ElevenLabs (`ulaw_8000` output, no resampling)
- **Server:** FastAPI + WebSockets

**Why this stack works for phone**

Twilio sends 8kHz mulaw audio. Universal-3 Pro Streaming accepts `pcm_mulaw` at `sample_rate=8000` natively. ElevenLabs returns `ulaw_8000` directly. The entire audio path stays in mulaw — zero resampling, which is where cheap latency lives.

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
 U3 Pro       (ulaw_8000)
 Streaming
   │             ▲
   │ transcript  │ audio
   ▼             │
   GPT-4o + tools
   (get_order_status, transfer_to_human)
```

---

## Quickstart

### 1. Install dependencies

```bash
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
- `ELEVENLABS_VOICE_ID` — optional, defaults to a clear neutral voice

### 3. Run the server

```bash
uvicorn server:app --port 8000
```

### 4. Expose with ngrok

In a second terminal:

```bash
ngrok http 8000
```

Copy the `https://*.ngrok-free.dev` URL ngrok prints.

### 5. Point a Twilio number at the server

In the [Twilio console](https://console.twilio.com), pick a Voice-enabled number. Under **A call comes in**, set the webhook to:

```
https://your-ngrok-url/twilio/voice
```

with method `POST`. Save.

### 6. Call it

Call the Twilio number from your phone. You'll hear the agent answer in natural conversation, transcribe what you say, look up orders, and offer to transfer to a human if you ask.

---

## What's in each file

| File | Purpose |
|---|---|
| `server.py` | FastAPI app with the TwiML endpoint and `/media-stream` WebSocket bridge. Pumps audio Twilio → AssemblyAI and TTS → Twilio. |
| `tools.py` | Tool definitions (`get_order_status`, `transfer_to_human`) and dispatcher. Replace stubs with real CRM / order-system calls. |
| `requirements.txt` | Python deps. |
| `.env.example` | Required environment variables. |

---

## Latency notes

A well-tuned run on this stack lands at **600–1100ms** from caller-stops-talking to caller-hears-reply. Where the budget goes:

| Stage | Typical latency |
|---|---|
| AssemblyAI end-of-turn finalization | 150–250ms |
| GPT-4o first-token | 200–400ms |
| ElevenLabs first audio byte | 200–400ms |
| Twilio round-trip | 50–100ms |

Watch out for:

- **Resampling** — keep audio in mulaw end-to-end (Universal-3 Pro Streaming `encoding=pcm_mulaw`, ElevenLabs `output_format=ulaw_8000`)
- **Slow tool calls** — anything over 500ms causes audible silence; cache and timeout
- **Non-streaming TTS** — `eleven.text_to_speech.stream` chunks audio out as it's generated

---

## Cost

- AssemblyAI Universal-3 Pro Streaming: **$0.15/hour** flat, unlimited concurrency
- OpenAI GPT-4o: pay-per-token (typically pennies per minute of conversation)
- ElevenLabs streaming TTS: per-character or per-minute depending on plan
- Twilio voice: per-minute, varies by country

End-to-end, expect a few cents per minute at production scale.

---

## Going to production

- Replace the in-memory `conversation` list with a per-call session store (Redis, Postgres, or a managed session service)
- Add PII redaction on transcripts before they hit your CRM/logs (AssemblyAI supports inline PII handling)
- Set up call recording with proper consent flows (two-party consent states require disclosure)
- Add timeout and retry logic on tool calls — never let a slow database call eat the turn
- Implement graceful handoff to a human agent with full conversation context attached

---

## License

MIT. Fork it, ship it.
