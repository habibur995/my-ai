"""Official Google GenAI (>= 3.5.0) provider adapter using Interactions API."""

from __future__ import annotations

import asyncio
import base64
import threading
from typing import Any, AsyncIterator

from google import genai
from google.genai import types

from backend.providers.base import (
    ModelInfo,
    ProviderAuthError,
    ProviderError,
    RateLimitError,
)
from backend.providers.catalog import CURATED_MODELS

# Paid-only or deprecated prefixes we never offer in Free-Only Hub
_EXCLUDED_PREFIXES = (
    "gemini-1.",
    "gemini-2.",
    "gemini-4-carbon",
    "gemini-4-neon",
    "gemini-4-argon",
    "gemini-3.1-pro",
    "gemini-3-pro",
    "gemini-pro",
    "veo-",
    "imagen-",
    "lyria-",
    "genie-",
    "aqa",
)
_EXCLUDED_SUBSTRINGS = (
    "embedding",
    "-tts",
    "-image",
    "nano-banana",
    "transcribe",
    "translate",
    "-live",
    "omni-",
    "decision",
    "robotics",
    "computer-use",
)


def _classify_gemini_error(exc: Exception) -> ProviderError:
    msg = str(exc)
    lower = msg.lower()
    if any(
        tok in lower
        for tok in ("429", "resource_exhausted", "rate limit", "quota", "too many requests")
    ):
        return RateLimitError(f"Gemini free quota/rate limit reached: {msg[:240]}")
    if any(
        tok in lower
        for tok in (
            "401",
            "403",
            "api_key_invalid",
            "api key not valid",
            "permission_denied",
            "unauthenticated",
        )
    ):
        return ProviderAuthError(f"Invalid or unauthorized Gemini API key: {msg[:240]}")
    return ProviderError(f"Gemini API error: {msg[:260]}")


def _merge_discovered_models(api_models: list[Any]) -> list[ModelInfo]:
    curated_by_id = {m.id: m for m in CURATED_MODELS["gemini"]}
    discovered: list[ModelInfo] = []
    seen: set[str] = set()

    for item in api_models:
        raw_name = getattr(item, "name", "") or ""
        model_id = raw_name.removeprefix("models/").strip()
        if not model_id or model_id in seen:
            continue
        lower = model_id.lower()
        if not (lower.startswith("gemini-") or lower.startswith("gemma-")):
            continue
        if any(lower.startswith(p) for p in _EXCLUDED_PREFIXES):
            continue
        if any(sub in lower for sub in _EXCLUDED_SUBSTRINGS):
            continue

        actions = getattr(item, "supported_actions", None) or []
        if actions and "generateContent" not in actions and "createInteraction" not in actions:
            continue

        seen.add(model_id)
        if model_id in curated_by_id:
            discovered.append(curated_by_id[model_id])
        else:
            display = getattr(item, "display_name", None) or model_id
            is_flash = "flash" in lower
            is_lite = "lite" in lower
            is_gemma = "gemma" in lower
            discovered.append(
                ModelInfo(
                    id=model_id,
                    label=str(display),
                    provider_id="gemini",
                    tags=["Free Tier", "Fast" if is_flash else ("Open" if is_gemma else "Gemini")],
                    context_k=1000 if "gemini" in lower else 128,
                    vision=not is_gemma or "26b" in lower or "12b" in lower,
                    coding_rank=84 if not is_lite else 78,
                    speed_rank=95 if is_lite else (90 if is_flash else 82),
                    free_verified=True,
                )
            )

    # Ensure primary curated free models remain available in sensible order
    if not discovered:
        return list(CURATED_MODELS["gemini"])

    # Prefer curated rank ordering first, then extra discovered models
    order_map = {m.id: idx for idx, m in enumerate(CURATED_MODELS["gemini"])}
    discovered.sort(key=lambda m: (order_map.get(m.id, 999), -m.speed_rank))
    return discovered


async def test_and_discover(api_key: str) -> tuple[bool, str, list[ModelInfo]]:
    """Validate a Gemini API key and discover supported free-tier models."""
    if not api_key.strip():
        return False, "Gemini API key is required.", []

    def _run() -> list[ModelInfo]:
        client = genai.Client(api_key=api_key.strip())
        models = list(client.models.list())
        return _merge_discovered_models(models)

    try:
        models = await asyncio.to_thread(_run)
        return True, f"Connected ({len(models)} free Gemini/Gemma models ready)", models
    except Exception as exc:
        err = _classify_gemini_error(exc)
        return False, str(err), []


def _format_attachments_for_interaction(
    text: str, attachments: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    text_parts: list[str] = []
    if text:
        text_parts.append(text)

    images: list[dict[str, Any]] = []
    for att in attachments or []:
        mime = str(att.get("mime_type", ""))
        name = str(att.get("name", "file"))
        data = att.get("data", "")
        if mime.startswith("image/") and isinstance(data, str) and data:
            images.append({"type": "image", "data": data, "mime_type": mime})
        elif isinstance(att.get("text_content"), str) and att["text_content"]:
            text_parts.append(f"\n[Attached file: {name}]\n{att['text_content'][:40000]}")
        elif data and (mime.startswith("text/") or mime in ("application/json", "application/xml")):
            try:
                decoded = base64.b64decode(data).decode("utf-8", errors="replace")
                text_parts.append(f"\n[Attached file: {name}]\n{decoded[:40000]}")
            except Exception:
                pass

    combined = "\n".join(text_parts).strip()
    if combined or not images:
        content.append({"type": "text", "text": combined or "Describe this image."})
    content.extend(images)
    return content


def _build_interaction_steps(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        text = str(msg.get("content", "") or "")
        atts = msg.get("attachments") or []
        if role == "assistant":
            if text:
                steps.append(
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": text}],
                    }
                )
        else:
            steps.append(
                {
                    "type": "user_input",
                    "content": _format_attachments_for_interaction(text, atts),
                }
            )
    return steps


def _build_generate_contents(
    messages: list[dict[str, Any]],
) -> list[types.Content]:
    contents: list[types.Content] = []
    for msg in messages:
        role = "model" if msg.get("role") == "assistant" else "user"
        text = str(msg.get("content", "") or "")
        atts = msg.get("attachments") or []
        parts: list[types.Part] = []

        if role == "user":
            blocks = _format_attachments_for_interaction(text, atts)
            for b in blocks:
                if b["type"] == "text":
                    parts.append(types.Part.from_text(text=b["text"]))
                elif b["type"] == "image":
                    raw = base64.b64decode(b["data"])
                    parts.append(types.Part.from_bytes(data=raw, mime_type=b["mime_type"]))
        else:
            parts.append(types.Part.from_text(text=text))

        if parts:
            contents.append(types.Content(role=role, parts=parts))
    return contents


async def stream_chat(
    *,
    api_key: str,
    model_id: str,
    messages: list[dict[str, Any]],
    system_instruction: str = "",
) -> AsyncIterator[str]:
    """Stream text deltas from Gemini via the Interactions API (with Gemma fallback)."""
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    cancel_flag = threading.Event()

    def _worker() -> None:
        emitted_any = False
        try:
            client = genai.Client(api_key=api_key.strip())
            steps = _build_interaction_steps(messages)

            try:
                kwargs: dict[str, Any] = {
                    "model": model_id,
                    "input": steps,
                    "store": False,
                    "stream": True,
                }
                if system_instruction.strip():
                    kwargs["system_instruction"] = system_instruction.strip()

                stream = client.interactions.create(**kwargs)
                for event in stream:
                    if cancel_flag.is_set():
                        break
                    if getattr(event, "event_type", None) == "step.delta":
                        delta = getattr(event, "delta", None)
                        if delta is not None and getattr(delta, "type", None) == "text":
                            txt = getattr(delta, "text", None) or ""
                            if txt:
                                emitted_any = True
                                loop.call_soon_threadsafe(queue.put_nowait, ("delta", txt))
            except Exception as primary_exc:
                # Some open models (like Gemma 4) or previews may require generate_content_stream
                if emitted_any:
                    raise
                lower = str(primary_exc).lower()
                if any(
                    tok in lower
                    for tok in ("429", "resource_exhausted", "401", "403", "api_key_invalid")
                ):
                    raise
                cfg = (
                    types.GenerateContentConfig(system_instruction=system_instruction.strip())
                    if system_instruction.strip()
                    else None
                )
                contents = _build_generate_contents(messages)
                for chunk in client.models.generate_content_stream(
                    model=model_id,
                    contents=contents,
                    config=cfg,
                ):
                    if cancel_flag.is_set():
                        break
                    txt = getattr(chunk, "text", None) or ""
                    if txt:
                        emitted_any = True
                        loop.call_soon_threadsafe(queue.put_nowait, ("delta", txt))

            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", _classify_gemini_error(exc)))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    try:
        while True:
            kind, payload = await queue.get()
            if kind == "delta":
                yield payload
            elif kind == "error":
                raise payload
            elif kind == "done":
                break
    finally:
        cancel_flag.set()


async def transcribe_audio(
    *,
    api_key: str,
    audio_bytes: bytes,
    mime_type: str = "audio/wav",
    language: str = "auto",
) -> str:
    """Transcribe short audio speech using Gemini Flash-Lite when Groq STT isn't configured."""
    lang_hint = (
        "Detect the spoken language automatically (e.g., Bengali, English, Hindi, Arabic) and transcribe faithfully."
        if language == "auto"
        else f"The spoken language is likely {language}. Transcribe faithfully."
    )
    prompt = f"{lang_hint} Return ONLY the exact spoken transcript text with no commentary."
    b64 = base64.b64encode(audio_bytes).decode("ascii")

    def _run() -> str:
        client = genai.Client(api_key=api_key.strip())
        interaction = client.interactions.create(
            model="gemini-3.5-flash-lite",
            store=False,
            input=[
                {"type": "text", "text": prompt},
                {"type": "audio", "data": b64, "mime_type": mime_type},
            ],
        )
        return (interaction.output_text or "").strip()

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        raise _classify_gemini_error(exc) from exc
