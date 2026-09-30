"""
Twilio + AssemblyAI voice agent.

Inbound calls to your Twilio number hit /twilio/voice, which returns TwiML
that opens a Media Stream to /media-stream. From there, audio flows in both
directions: Twilio -> AssemblyAI Universal-3.6 Pro Realtime for transcription,
GPT-4o for reasoning and tool calls, ElevenLabs for TTS, and back to Twilio as
mulaw frames.

All audio stays in 8kHz mulaw end-to-end. Zero resampling.
"""

import asyncio
import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import websockets
from dotenv import load_dotenv
from elevenlabs.client import AsyncElevenLabs
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from openai import AsyncOpenAI

from tools import TOOLS, dispatch_tool

load_dotenv()

ASSEMBLYAI_API_KEY = os.environ["ASSEMBLYAI_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]
# ELEVEN_VOICE_ID matches the blog post; ELEVENLABS_VOICE_ID is still accepted.
ELEVEN_VOICE_ID = (
    os.environ.get("ELEVEN_VOICE_ID")
    or os.environ.get("ELEVENLABS_VOICE_ID")
    or "EXAVITQu4vr4xnSDxMaL"
)

# Universal-3.6 Pro Realtime over the v3 streaming socket.
# Streaming takes the singular `speech_model` (async takes a plural list).
# voice_focus=near-field isolates the primary speaker on a phone handset.
# There is no format_turns flag: turns always come back punctuated and cased.
ASSEMBLYAI_WS_URL = (
    "wss://streaming.assemblyai.com/v3/ws"
    "?speech_model=universal-3-6-pro"
    "&encoding=pcm_mulaw"
    "&sample_rate=8000"
    "&voice_focus=near-field"
)

GREETING = "Thanks for calling ACME Footwear. How can I help?"

SYSTEM_PROMPT = (
    "You are a friendly phone-based voice agent for ACME Footwear. "
    "Keep replies short — one or two sentences. Conversational, not formal. "
    "Use get_order_status when the caller asks about an order. "
    "Use transfer_to_human if the caller asks for a person, is upset, "
    "or asks something outside order status. "
    "Callers may type order IDs on the keypad; those arrive as "
    "'[caller entered: ...]'."
)

app = FastAPI()
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
eleven_client = AsyncElevenLabs(api_key=ELEVENLABS_API_KEY)


@dataclass
class CallSession:
    aai_ws: Any = None
    stream_sid: Optional[str] = None
    conversation: list = field(default_factory=lambda: [
        {"role": "system", "content": SYSTEM_PROMPT}
    ])
    dtmf_buffer: list = field(default_factory=list)
    speaking: bool = False
    speak_task: Optional[asyncio.Task] = None
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@app.post("/twilio/voice")
async def twilio_voice(request: Request):
    host = request.headers["host"]
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://{host}/media-stream" />
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/media-stream")
async def media_stream(twilio_ws: WebSocket):
    await twilio_ws.accept()
    session = CallSession()

    async with websockets.connect(
        ASSEMBLYAI_WS_URL,
        additional_headers={"Authorization": ASSEMBLYAI_API_KEY},
    ) as aai_ws:
        session.aai_ws = aai_ws
        try:
            await asyncio.gather(
                _pump_twilio_to_aai(twilio_ws, aai_ws, session),
                _pump_aai_to_llm(aai_ws, twilio_ws, session),
            )
        except (WebSocketDisconnect, websockets.ConnectionClosed):
            pass
        finally:
            if session.speak_task:
                session.speak_task.cancel()


async def _pump_twilio_to_aai(twilio_ws, aai_ws, session):
    async for raw in twilio_ws.iter_text():
        event = json.loads(raw)
        kind = event.get("event")
        if kind == "start":
            session.stream_sid = event["start"]["streamSid"]
            # Greet first so the caller hears something immediately
            session.conversation.append({"role": "assistant", "content": GREETING})
            await say(GREETING, twilio_ws, session)
        elif kind == "media":
            await aai_ws.send(base64.b64decode(event["media"]["payload"]))
        elif kind == "dtmf":
            await handle_dtmf(event["dtmf"]["digit"], twilio_ws, session)
        elif kind == "stop":
            await aai_ws.close()
            return


async def _pump_aai_to_llm(aai_ws, twilio_ws, session):
    async for message in aai_ws:
        data = json.loads(message)
        if data.get("type") != "Turn":
            continue
        transcript = (data.get("transcript") or "").strip()
        if not transcript:
            continue
        # Barge-in: the caller started talking while the agent is speaking.
        if session.speaking:
            await barge_in(twilio_ws, session)
        if data.get("end_of_turn"):
            asyncio.create_task(handle_user_turn(transcript, twilio_ws, session))


async def handle_dtmf(digit, twilio_ws, session):
    """Buffer keypad digits; '#' submits, '*' clears."""
    if digit == "#":
        collected = "".join(session.dtmf_buffer)
        session.dtmf_buffer.clear()
        if collected:
            asyncio.create_task(handle_user_turn(
                f"[caller entered: {collected}]", twilio_ws, session
            ))
    elif digit == "*":
        session.dtmf_buffer.clear()
    else:
        session.dtmf_buffer.append(digit)


async def handle_user_turn(transcript, twilio_ws, session):
    async with session.turn_lock:
        session.conversation.append({"role": "user", "content": transcript})

        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=session.conversation,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.4,
        )
        msg = response.choices[0].message

        if msg.tool_calls:
            session.conversation.append(msg.model_dump(exclude_unset=True))
            for call in msg.tool_calls:
                result = await dispatch_tool(
                    call.function.name, call.function.arguments
                )
                session.conversation.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                })
            followup = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=session.conversation,
                temperature=0.4,
            )
            reply = followup.choices[0].message.content
        else:
            reply = msg.content

        session.conversation.append({"role": "assistant", "content": reply})
        await say(reply, twilio_ws, session)


async def say(text, twilio_ws, session):
    """Tell AssemblyAI what the agent just asked, then speak it."""
    if not text:
        return
    await send_agent_context(text, session)
    if session.speak_task and not session.speak_task.done():
        session.speak_task.cancel()
    session.speak_task = asyncio.create_task(speak(text, twilio_ws, session))


async def send_agent_context(text, session):
    """agent_context tells the model what kind of answer to expect next
    (a zip code, an order ID, a yes/no), which helps short, entity-heavy
    replies resolve correctly."""
    if session.aai_ws is None:
        return
    await session.aai_ws.send(json.dumps({
        "type": "UpdateConfiguration",
        "agent_context": text,
    }))


async def barge_in(twilio_ws, session):
    """Flush Twilio's playback buffer and stop synthesizing."""
    if session.speak_task and not session.speak_task.done():
        session.speak_task.cancel()
    session.speaking = False
    if session.stream_sid:
        await twilio_ws.send_text(json.dumps({
            "event": "clear",
            "streamSid": session.stream_sid,
        }))


async def speak(text, twilio_ws, session):
    if not session.stream_sid or not text:
        return
    session.speaking = True
    try:
        audio_stream = eleven_client.text_to_speech.stream(
            voice_id=ELEVEN_VOICE_ID,
            text=text,
            model_id="eleven_turbo_v2_5",
            output_format="ulaw_8000",
        )
        async for chunk in audio_stream:
            payload = base64.b64encode(chunk).decode()
            await twilio_ws.send_text(json.dumps({
                "event": "media",
                "streamSid": session.stream_sid,
                "media": {"payload": payload},
            }))
    finally:
        session.speaking = False
