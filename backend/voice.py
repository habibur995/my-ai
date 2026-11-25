"""Dual Multilingual Live Streaming Voice Engine for FreeAI Hub.

Engine A: Gemini 3.8 Live WebSocket (`gemini-3.8-live`) — native bidirectional audio + transcription.
Engine B: Universal Free Voice — Groq Whisper / Gemini STT -> Auto-Routed Free LLM -> Edge-TTS neural audio.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import wave
from typing import Any

import edge_tts
from fastapi import WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from backend.db import Database
from backend.providers import gemini_provider, openai_compat
from backend.providers.base import ProviderError
from backend.router import stream_chat_with_routing

# Multilingual neural voice table for Edge-TTS (100% free, natural Bengali + 70+ languages)
EDGE_VOICE_MAP: dict[str, dict[str, str]] = {
    "bn": {"female": "bn-BD-NabanitaNeural", "male": "bn-BD-PradeepNeural"},
    "en": {"female": "en-US-AriaNeural", "male": "en-US-GuyNeural"},
    "hi": {"female": "hi-IN-SwaraNeural", "male": "hi-IN-MadhurNeural"},
    "ar": {"female": "ar-SA-ZariyahNeural", "male": "ar-SA-HamedNeural"},
    "ur": {"female": "ur-PK-UzmaNeural", "male": "ur-PK-AsadNeural"},
    "es": {"female": "es-ES-ElviraNeural", "male": "es-ES-AlvaroNeural"},
    "fr": {"female": "fr-FR-DeniseNeural", "male": "fr-FR-HenriNeural"},
    "de": {"female": "de-DE-KatjaNeural", "male": "de-DE-ConradNeural"},
    "ja": {"female": "ja-JP-NanamiNeural", "male": "ja-JP-KeitaNeural"},
    "ko": {"female": "ko-KR-SunHiNeural", "male": "ko-KR-InJoonNeural"},
    "zh": {"female": "zh-CN-XiaoxiaoNeural", "male": "zh-CN-YunxiNeural"},
    "pt": {"female": "pt-BR-FranciscaNeural", "male": "pt-BR-AntonioNeural"},
}

_SCRIPT_DETECTORS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\u0980-\u09FF]"), "bn"),  # Bengali
    (re.compile(r"[\u0900-\u097F]"), "hi"),  # Devanagari / Hindi
    (re.compile(r"[\u0600-\u06FF]"), "ar"),  # Arabic / Urdu script
    (re.compile(r"[\u3040-\u30FF]"), "ja"),  # Japanese Hiragana/Katakana
    (re.compile(r"[\uAC00-\uD7AF]"), "ko"),  # Korean Hangul
    (re.compile(r"[\u4E00-\u9FFF]"), "zh"),  # Chinese Han
]

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[।.!?।\n])\s+")
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def select_edge_voice(text: str, language: str = "auto", gender: str = "female") -> str:
    """Pick a natural neural Edge-TTS voice matching explicit language or detected script."""
    g = "male" if str(gender).lower() == "male" else "female"
    lang_code = (language or "auto").strip().lower().split("-")[0]

    # When explicit language is set and known, use it unless the text is clearly in another non-Latin script
    detected = ""
    for pattern, code in _SCRIPT_DETECTORS:
        if pattern.search(text or ""):
            detected = code
            break

    if lang_code != "auto" and lang_code in EDGE_VOICE_MAP:
        if detected and detected != lang_code and detected in EDGE_VOICE_MAP:
            return EDGE_VOICE_MAP[detected][g]
        return EDGE_VOICE_MAP[lang_code][g]

    if detected and detected in EDGE_VOICE_MAP:
        return EDGE_VOICE_MAP[detected][g]

    return EDGE_VOICE_MAP["en"][g]


def clean_text_for_tts(text: str) -> str:
    """Strip Markdown syntax and code blocks so speech sounds natural."""
    cleaned = _CODE_BLOCK_RE.sub(" কোড স্ক্রিনে দেওয়া হয়েছে। " if re.search(r"[\u0980-\u09FF]", text) else " (Code shown on screen.) ", text)
    cleaned = _MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"[`*_~#>|]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


async def synthesize_edge_tts(
    text: str,
    *,
    language: str = "auto",
    gender: str = "female",
) -> tuple[bytes, str]:
    """Synthesize natural speech MP3 bytes using free Edge-TTS."""
    spoken = clean_text_for_tts(text)
    if not spoken:
        return b"", ""
    voice = select_edge_voice(spoken, language=language, gender=gender)
    comm = edge_tts.Communicate(spoken, voice)
    chunks: list[bytes] = []
    async for msg in comm.stream():
        if msg.get("type") == "audio" and msg.get("data"):
            chunks.append(msg["data"])
    return b"".join(chunks), voice


def pcm16_to_wav_bytes(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap raw 16-bit mono little-endian PCM bytes in a standard RIFF WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


async def _transcribe_pcm_buffer(
    db: Database,
    pcm_bytes: bytes,
    language: str = "auto",
) -> tuple[str, str]:
    """Transcribe PCM audio using Groq Whisper (primary) or Gemini Flash-Lite (secondary)."""
    if len(pcm_bytes) < 1600:  # < 50ms of audio
        return "", ""

    wav_bytes = pcm16_to_wav_bytes(pcm_bytes, sample_rate=16000)

    groq_secret = db.get_provider_secret("groq")
    if groq_secret and groq_secret["enabled"] and groq_secret["api_key"]:
        try:
            text = await openai_compat.transcribe_groq(
                api_key=groq_secret["api_key"],
                audio_bytes=wav_bytes,
                filename="speech.wav",
                language=language,
            )
            return text, "Groq Whisper v3 Turbo"
        except Exception:
            pass

    gemini_secret = db.get_provider_secret("gemini")
    if gemini_secret and gemini_secret["enabled"] and gemini_secret["api_key"]:
        text = await gemini_provider.transcribe_audio(
            api_key=gemini_secret["api_key"],
            audio_bytes=wav_bytes,
            mime_type="audio/wav",
            language=language,
        )
        return text, "Gemini 3.5 Flash-Lite STT"

    raise ProviderError(
        "Voice audio transcription requires either a Groq key (Whisper) or a Gemini key, or use browser speech mode."
    )


async def _safe_send_json(ws: WebSocket, payload: dict[str, Any]) -> bool:
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False


def _pop_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """Split completed sentences for low-latency incremental TTS synthesis."""
    parts = _SENTENCE_SPLIT_RE.split(buffer)
    if len(parts) <= 1:
        # Flush if buffer grows very long without punctuation
        if len(buffer) >= 220:
            cut = buffer.rfind(" ", 0, 180)
            if cut > 60:
                return [buffer[:cut].strip()], buffer[cut:].lstrip()
        return [], buffer
    ready = [p.strip() for p in parts[:-1] if p.strip()]
    return ready, parts[-1]


async def handle_voice_websocket(ws: WebSocket, db: Database) -> None:
    """Main WebSocket handler supporting Auto / Gemini 3.8 Live / Universal Free Voice."""
    await ws.accept()

    try:
        raw_init = await ws.receive_text()
        init_cfg = json.loads(raw_init)
    except (WebSocketDisconnect, json.JSONDecodeError, Exception):
        return

    settings = db.get_settings_raw()
    requested_engine = str(init_cfg.get("engine") or settings.get("voice_engine") or "auto")
    language = str(init_cfg.get("language") or settings.get("voice_language") or "auto")
    gender = str(init_cfg.get("gender") or settings.get("voice_gender") or "female")
    mode = str(init_cfg.get("mode") or "auto")
    selected_provider = str(init_cfg.get("provider_id") or "")
    selected_model = str(init_cfg.get("model_id") or "")
    conversation_id = str(init_cfg.get("conversation_id") or "")

    gemini_secret = db.get_provider_secret("gemini")
    has_gemini = bool(gemini_secret and gemini_secret["enabled"] and gemini_secret["api_key"])

    if requested_engine == "gemini_live" and not has_gemini:
        await _safe_send_json(
            ws,
            {
                "type": "error",
                "message": "Gemini Live requires a connected Google AI Studio API key.",
            },
        )
        return

    # Decide initial engine: try Gemini 3.8 Live if requested or auto (when manual model isn't non-Gemini)
    try_gemini_live = False
    if requested_engine == "gemini_live":
        try_gemini_live = True
    elif requested_engine == "auto" and has_gemini:
        if mode == "auto" or selected_provider in ("", "gemini"):
            try_gemini_live = True

    if try_gemini_live and gemini_secret:
        fallback_reason = await _run_gemini_live_bridge(
            ws=ws,
            db=db,
            api_key=gemini_secret["api_key"],
            language=language,
            system_prompt=settings.get("system_prompt", ""),
            conversation_id=conversation_id,
        )
        if fallback_reason is None:
            return
        # Seamless fallback to Universal Free Voice!
        await _safe_send_json(
            ws,
            {
                "type": "engine_fallback",
                "engine": "universal",
                "reason": fallback_reason,
            },
        )

    await _run_universal_voice_loop(
        ws=ws,
        db=db,
        language=language,
        gender=gender,
        mode=mode,
        selected_provider=selected_provider,
        selected_model=selected_model,
        system_prompt=settings.get("system_prompt", ""),
        conversation_id=conversation_id,
    )


async def _run_gemini_live_bridge(
    *,
    ws: WebSocket,
    db: Database,
    api_key: str,
    language: str,
    system_prompt: str,
    conversation_id: str,
) -> str | None:
    """Bridge client audio/text over WebSocket to Gemini 3.8 Live.

    Returns None when ended cleanly by client, or a fallback reason string if
    Gemini Live failed before completing a live turn.
    """
    lang_note = (
        "Detect the user's spoken language automatically (including Bengali, English, Hindi, Arabic, etc.) and always reply naturally in that same language."
        if language == "auto"
        else f"Always speak and respond in language code '{language}' unless the user asks you to switch."
    )
    full_instruction = f"{system_prompt}\n\n{lang_note}\nKeep spoken replies concise, warm, and conversational.".strip()

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=types.Content(parts=[types.Part(text=full_instruction)]),
    )

    client = genai.Client(
        api_key=api_key.strip(),
        http_options={"api_version": "v1beta"},
    )
    completed_any_turn = False
    user_transcript_buf: list[str] = []
    assistant_transcript_buf: list[str] = []

    try:
        async with client.aio.live.connect(model="gemini-3.8-live", config=config) as session:
            await _safe_send_json(
                ws,
                {
                    "type": "session_ready",
                    "engine": "gemini_live",
                    "model": "gemini-3.8-live",
                    "label": "Gemini 3.8 Live (Native Audio)",
                },
            )

            stop_event = asyncio.Event()

            async def _client_to_gemini() -> None:
                while not stop_event.is_set():
                    try:
                        msg = await ws.receive()
                    except WebSocketDisconnect:
                        stop_event.set()
                        break

                    if msg.get("type") == "websocket.disconnect":
                        stop_event.set()
                        break

                    raw_bytes = msg.get("bytes")
                    if raw_bytes:
                        await session.send_realtime_input(
                            audio=types.Blob(data=raw_bytes, mime_type="audio/pcm;rate=16000")
                        )
                        continue

                    raw_text = msg.get("text")
                    if not raw_text:
                        continue
                    try:
                        data = json.loads(raw_text)
                    except json.JSONDecodeError:
                        continue

                    ev_type = data.get("type")
                    if ev_type == "stop":
                        stop_event.set()
                        break
                    elif ev_type == "audio_end":
                        await session.send_realtime_input(audio_stream_end=True)
                    elif ev_type == "text" and data.get("text"):
                        await session.send_realtime_input(text=str(data["text"]))
                    elif ev_type == "interrupt":
                        pass
                    elif ev_type == "ping":
                        await _safe_send_json(ws, {"type": "pong"})

            async def _gemini_to_client() -> None:
                nonlocal completed_any_turn
                while not stop_event.is_set():
                    received_any = False
                    async for response in session.receive():
                        received_any = True
                        if stop_event.is_set():
                            break
                        content = getattr(response, "server_content", None)
                        if not content:
                            continue

                        if getattr(content, "interrupted", False):
                            assistant_transcript_buf.clear()
                            await _safe_send_json(ws, {"type": "interrupted"})

                        # Process ALL parts in each server event
                        model_turn = getattr(content, "model_turn", None)
                        if model_turn and getattr(model_turn, "parts", None):
                            for part in model_turn.parts:
                                inline = getattr(part, "inline_data", None)
                                if inline and getattr(inline, "data", None):
                                    raw_audio = inline.data
                                    if isinstance(raw_audio, bytes):
                                        b64 = base64.b64encode(raw_audio).decode("ascii")
                                    else:
                                        b64 = str(raw_audio)
                                    await _safe_send_json(
                                        ws,
                                        {
                                            "type": "audio_pcm",
                                            "sample_rate": 24000,
                                            "data": b64,
                                        },
                                    )

                        in_tr = getattr(content, "input_transcription", None)
                        if in_tr and getattr(in_tr, "text", None):
                            txt = in_tr.text
                            user_transcript_buf.append(txt)
                            await _safe_send_json(
                                ws,
                                {"type": "transcript", "role": "user", "text": txt, "final": False},
                            )

                        out_tr = getattr(content, "output_transcription", None)
                        if out_tr and getattr(out_tr, "text", None):
                            txt = out_tr.text
                            assistant_transcript_buf.append(txt)
                            await _safe_send_json(
                                ws,
                                {"type": "transcript", "role": "assistant", "text": txt, "final": False},
                            )

                        if getattr(content, "turn_complete", False):
                            completed_any_turn = True
                            u_full = "".join(user_transcript_buf).strip()
                            a_full = "".join(assistant_transcript_buf).strip()
                            if conversation_id and (u_full or a_full):
                                if u_full:
                                    db.add_message(conversation_id, "user", u_full, route_reason="Live Voice")
                                if a_full:
                                    db.add_message(
                                        conversation_id,
                                        "assistant",
                                        a_full,
                                        provider_id="gemini",
                                        model_id="gemini-3.8-live",
                                        route_reason="Gemini 3.8 Live",
                                    )
                            user_transcript_buf.clear()
                            assistant_transcript_buf.clear()
                            await _safe_send_json(ws, {"type": "turn_complete"})
                    if not received_any:
                        break

            t_in = asyncio.create_task(_client_to_gemini())
            t_out = asyncio.create_task(_gemini_to_client())
            done, pending = await asyncio.wait(
                [t_in, t_out],
                return_when=asyncio.FIRST_COMPLETED,
            )
            stop_event.set()
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    raise exc
            return None
    except Exception as exc:
        if completed_any_turn:
            await _safe_send_json(
                ws,
                {"type": "error", "message": f"Gemini Live disconnected: {exc}"},
            )
            return None
        return f"Gemini 3.8 Live unavailable ({str(exc)[:140]}); switched to Universal Free Voice."


async def _run_universal_voice_loop(
    *,
    ws: WebSocket,
    db: Database,
    language: str,
    gender: str,
    mode: str,
    selected_provider: str,
    selected_model: str,
    system_prompt: str,
    conversation_id: str,
) -> None:
    """Universal Free Voice loop: PCM/Browser-STT -> Auto-Routed LLM -> Sentence-streamed Edge-TTS."""
    groq_secret = db.get_provider_secret("groq")
    gemini_secret = db.get_provider_secret("gemini")
    has_server_stt = bool(
        (groq_secret and groq_secret["enabled"] and groq_secret["api_key"])
        or (gemini_secret and gemini_secret["enabled"] and gemini_secret["api_key"])
    )

    await _safe_send_json(
        ws,
        {
            "type": "session_ready",
            "engine": "universal",
            "has_server_stt": has_server_stt,
            "label": "Universal Free Voice (Multilingual)",
        },
    )

    audio_buffer = bytearray()
    active_task: asyncio.Task[None] | None = None
    voice_history: list[dict[str, Any]] = []
    if conversation_id:
        for m in db.list_messages(conversation_id)[-12:]:
            voice_history.append({"role": m["role"], "content": m["content"]})

    async def _cancel_active() -> None:
        nonlocal active_task
        if active_task and not active_task.done():
            active_task.cancel()
            try:
                await active_task
            except (asyncio.CancelledError, Exception):
                pass
        active_task = None

    async def _process_turn(user_text: str) -> None:
        clean_text = user_text.strip()
        if not clean_text:
            await _safe_send_json(ws, {"type": "turn_complete"})
            return

        voice_history.append({"role": "user", "content": clean_text})
        connected_items = db.get_connected_models()
        settings = db.get_settings_raw()
        auto_fb = settings.get("auto_fallback", "true").lower() == "true"

        lang_directive = (
            "Respond naturally in the exact same language the user spoke (e.g., Bengali or English). Keep spoken responses concise and clear."
            if language == "auto"
            else f"Respond concisely in language code '{language}'."
        )
        full_sys = f"{system_prompt}\n\n{lang_directive}".strip()

        full_reply_parts: list[str] = []
        tts_buffer = ""
        used_provider = ""
        used_model = ""
        used_reason = ""

        try:
            async for event in stream_chat_with_routing(
                connected_items,
                messages=voice_history,
                system_instruction=full_sys,
                mode=mode,
                selected_provider=selected_provider,
                selected_model=selected_model,
                auto_fallback=auto_fb,
            ):
                if event["type"] == "meta":
                    used_provider = event["provider_id"]
                    used_model = event["model_id"]
                    used_reason = event["route_reason"]
                    await _safe_send_json(ws, event)
                elif event["type"] == "delta":
                    delta = event["text"]
                    full_reply_parts.append(delta)
                    tts_buffer += delta
                    await _safe_send_json(
                        ws,
                        {"type": "transcript", "role": "assistant", "text": delta, "final": False},
                    )
                    sentences, tts_buffer = _pop_complete_sentences(tts_buffer)
                    for sent in sentences:
                        mp3_bytes, voice_name = await synthesize_edge_tts(
                            sent, language=language, gender=gender
                        )
                        if mp3_bytes:
                            await _safe_send_json(
                                ws,
                                {
                                    "type": "audio_mp3",
                                    "voice": voice_name,
                                    "data": base64.b64encode(mp3_bytes).decode("ascii"),
                                },
                            )

            if tts_buffer.strip():
                mp3_bytes, voice_name = await synthesize_edge_tts(
                    tts_buffer.strip(), language=language, gender=gender
                )
                if mp3_bytes:
                    await _safe_send_json(
                        ws,
                        {
                            "type": "audio_mp3",
                            "voice": voice_name,
                            "data": base64.b64encode(mp3_bytes).decode("ascii"),
                        },
                    )

            full_reply = "".join(full_reply_parts).strip()
            if full_reply:
                voice_history.append({"role": "assistant", "content": full_reply})
                if conversation_id:
                    db.add_message(conversation_id, "user", clean_text, route_reason="Live Voice")
                    db.add_message(
                        conversation_id,
                        "assistant",
                        full_reply,
                        provider_id=used_provider,
                        model_id=used_model,
                        route_reason=f"Voice · {used_reason}",
                    )
            await _safe_send_json(ws, {"type": "turn_complete"})
        except asyncio.CancelledError:
            await _safe_send_json(ws, {"type": "interrupted"})
            raise
        except Exception as exc:
            await _safe_send_json(ws, {"type": "error", "message": str(exc)})
            await _safe_send_json(ws, {"type": "turn_complete"})

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break

            raw_bytes = msg.get("bytes")
            if raw_bytes:
                audio_buffer.extend(raw_bytes)
                continue

            raw_text = msg.get("text")
            if not raw_text:
                continue
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                continue

            ev_type = data.get("type")
            if ev_type == "stop":
                break
            elif ev_type == "config":
                language = str(data.get("language") or language)
                gender = str(data.get("gender") or gender)
                mode = str(data.get("mode") or mode)
                selected_provider = str(data.get("provider_id") or "")
                selected_model = str(data.get("model_id") or "")
            elif ev_type == "interrupt":
                await _cancel_active()
                audio_buffer.clear()
                await _safe_send_json(ws, {"type": "interrupted"})
            elif ev_type == "audio_end":
                pcm_bytes = bytes(audio_buffer)
                audio_buffer.clear()
                if not pcm_bytes:
                    continue
                await _cancel_active()
                await _safe_send_json(ws, {"type": "status", "state": "transcribing"})
                try:
                    transcript, stt_engine = await _transcribe_pcm_buffer(
                        db, pcm_bytes, language=language
                    )
                except Exception as exc:
                    await _safe_send_json(ws, {"type": "error", "message": str(exc)})
                    await _safe_send_json(ws, {"type": "turn_complete"})
                    continue

                if not transcript:
                    await _safe_send_json(ws, {"type": "turn_complete"})
                    continue

                await _safe_send_json(
                    ws,
                    {
                        "type": "transcript",
                        "role": "user",
                        "text": transcript,
                        "final": True,
                        "stt": stt_engine,
                    },
                )
                active_task = asyncio.create_task(_process_turn(transcript))
            elif ev_type == "text" and data.get("text"):
                await _cancel_active()
                text_in = str(data["text"]).strip()
                await _safe_send_json(
                    ws,
                    {"type": "transcript", "role": "user", "text": text_in, "final": True},
                )
                active_task = asyncio.create_task(_process_turn(text_in))
            elif ev_type == "ping":
                await _safe_send_json(ws, {"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await _cancel_active()
