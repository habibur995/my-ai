"""Unified async OpenAI-compatible adapter for strictly-free tiers & local servers.

Supports Groq, OpenRouter (strict :free guard), NVIDIA NIM, Cohere, Hugging Face, and Ollama/LM Studio.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from backend.providers.base import (
    ModelInfo,
    ProviderAuthError,
    ProviderError,
    RateLimitError,
)
from backend.providers.catalog import CURATED_MODELS, PROVIDER_MAP

_GROQ_SKIP_KEYWORDS = ("whisper", "tts", "guard", "prompt-guard", "orpheus", "playai")


def is_openrouter_free(model_id: str, model_obj: dict[str, Any] | None = None) -> bool:
    """Strictly verify an OpenRouter model is free ($0/token or ':free' suffix)."""
    clean_id = model_id.strip()
    if clean_id == "openrouter/free" or clean_id.endswith(":free"):
        return True
    if model_obj and isinstance(model_obj.get("pricing"), dict):
        pricing = model_obj["pricing"]
        try:
            prompt_cost = float(pricing.get("prompt", 1))
            comp_cost = float(pricing.get("completion", 1))
            if prompt_cost == 0.0 and comp_cost == 0.0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _is_vision_model(model_id: str, model_obj: dict[str, Any] | None = None) -> bool:
    lower = model_id.lower()
    if any(k in lower for k in ("vision", "vl", "scout", "maverick", "pixtral", "gemini", "gemma-4-26b", "llava")):
        return True
    if model_obj:
        arch = model_obj.get("architecture") or {}
        modalities = arch.get("input_modalities") or []
        if "image" in modalities:
            return True
    return False


def _estimate_ranks(provider_id: str, model_id: str, context_k: int) -> tuple[int, int]:
    lower = model_id.lower()
    coding = 72
    speed = 78

    if any(k in lower for k in ("coder", "codestral", "deepseek-r1", "deepseek-v3", "qwen3", "qwen2.5-72b", "gpt-oss-120b")):
        coding = 88
    elif any(k in lower for k in ("70b", "72b", "command-a", "nemotron-70b")):
        coding = 84
    elif any(k in lower for k in ("8b", "7b", "3b", "mini", "instant", "lite")):
        coding = 66

    if provider_id == "groq":
        speed = 97 if any(k in lower for k in ("8b", "instant", "scout")) else 92
    elif provider_id == "ollama":
        speed = 86
    elif any(k in lower for k in ("mini", "flash", "instant", "lite", "8b")):
        speed = 90
    elif any(k in lower for k in ("120b", "405b", "70b", "72b")):
        speed = 76

    if context_k >= 256:
        coding += 2
    return min(99, coding), min(99, speed)


def _headers_for(provider_id: str, api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    if provider_id == "openrouter":
        headers["HTTP-Referer"] = "http://localhost:8000"
        headers["X-Title"] = "FreeAI Hub"
    return headers


def _classify_http_error(provider_id: str, status_code: int, body: str) -> ProviderError:
    snippet = body[:240].strip()
    if status_code in (401, 403):
        return ProviderAuthError(f"{provider_id} rejected the API key ({status_code}): {snippet}")
    if status_code in (429, 402):
        return RateLimitError(f"{provider_id} rate/free-quota limit reached ({status_code}): {snippet}")
    return ProviderError(f"{provider_id} HTTP {status_code}: {snippet}")


async def test_and_discover(
    provider_id: str,
    api_key: str,
    base_url: str,
) -> tuple[bool, str, list[ModelInfo]]:
    """Test connectivity/credentials and return only verified free/local models."""
    spec = PROVIDER_MAP[provider_id]
    clean_url = (base_url or spec.default_base_url).strip().rstrip("/")
    if spec.requires_key and not api_key.strip():
        return False, f"{spec.name} API key is required.", []

    headers = _headers_for(provider_id, api_key)
    curated_map = {m.id: m for m in CURATED_MODELS.get(provider_id, [])}

    try:
        async with httpx.AsyncClient(timeout=18.0) as client:
            resp = await client.get(f"{clean_url}/models", headers=headers)
            raw_models: list[dict[str, Any]] = []

            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, dict):
                    raw_models = payload.get("data") or payload.get("models") or []
                elif isinstance(payload, list):
                    raw_models = payload
            elif provider_id == "ollama":
                # Fallback to native Ollama /api/tags endpoint
                root_url = clean_url.removesuffix("/v1")
                tag_resp = await client.get(f"{root_url}/api/tags")
                if tag_resp.status_code == 200:
                    tags_data = tag_resp.json()
                    raw_models = [{"id": m["name"]} for m in tags_data.get("models", []) if "name" in m]
                else:
                    return False, f"Local server not reachable at {clean_url} ({resp.status_code}).", []
            else:
                return False, str(_classify_http_error(provider_id, resp.status_code, resp.text)), []

    except httpx.ConnectError:
        if provider_id == "ollama":
            return False, f"Ollama / LM Studio not running at {clean_url}. Start it locally first.", []
        return False, f"Network error connecting to {clean_url}.", []
    except Exception as exc:
        return False, f"Connection failed: {exc}", []

    discovered: list[ModelInfo] = []
    seen: set[str] = set()

    if provider_id == "openrouter":
        discovered.append(CURATED_MODELS["openrouter"][0])
        seen.add("openrouter/free")

    for obj in raw_models:
        if not isinstance(obj, dict):
            continue
        mid = str(obj.get("id") or obj.get("name") or "").strip()
        if not mid or mid in seen:
            continue

        lower = mid.lower()
        if provider_id == "groq" and any(k in lower for k in _GROQ_SKIP_KEYWORDS):
            continue
        if provider_id == "openrouter" and not is_openrouter_free(mid, obj):
            continue
        if any(k in lower for k in ("embed", "rerank", "moderation", "tts", "whisper", "transcribe", "speech")):
            continue

        seen.add(mid)
        if mid in curated_map:
            discovered.append(curated_map[mid])
            continue

        ctx_raw = obj.get("context_length") or obj.get("context_window") or 128000
        try:
            context_k = max(4, int(int(ctx_raw) / 1000))
        except (TypeError, ValueError):
            context_k = 128

        vision = _is_vision_model(mid, obj)
        coding_rank, speed_rank = _estimate_ranks(provider_id, mid, context_k)
        label = str(obj.get("name") or mid).removesuffix(" (free)").strip()
        if provider_id == "openrouter" and ":free" in mid and "(free)" not in label.lower():
            label = f"{label} (:free)"

        tags = [spec.badge]
        if vision:
            tags.append("Vision")
        if coding_rank >= 85:
            tags.append("Code")
        if speed_rank >= 90:
            tags.append("Fast")

        discovered.append(
            ModelInfo(
                id=mid,
                label=label,
                provider_id=provider_id,
                tags=tags,
                context_k=context_k,
                vision=vision,
                coding_rank=coding_rank,
                speed_rank=speed_rank,
                free_verified=True,
            )
        )

    # Cap huge catalogs (e.g., NVIDIA/HF) while preserving curated + top models
    if not discovered:
        discovered = list(CURATED_MODELS.get(provider_id, []))
    elif provider_id in ("nvidia", "huggingface", "openrouter"):
        curated_ids = {m.id for m in CURATED_MODELS.get(provider_id, [])}
        discovered.sort(
            key=lambda m: (0 if m.id in curated_ids else 1, -(m.coding_rank + m.speed_rank))
        )
        discovered = discovered[:35]

    if provider_id == "ollama" and not discovered:
        return True, "Ollama connected, but 0 models pulled yet (run: ollama pull gemma4).", []

    return True, f"Connected ({len(discovered)} free models ready)", discovered


def _build_openai_messages(
    messages: list[dict[str, Any]],
    system_instruction: str,
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    if system_instruction.strip():
        payload.append({"role": "system", "content": system_instruction.strip()})

    for msg in messages:
        role = "assistant" if msg.get("role") == "assistant" else "user"
        text = str(msg.get("content", "") or "")
        atts = msg.get("attachments") or []

        if role == "user" and atts:
            parts: list[dict[str, Any]] = []
            text_acc = [text] if text else []
            img_parts: list[dict[str, Any]] = []

            for att in atts:
                mime = str(att.get("mime_type", ""))
                name = str(att.get("name", "file"))
                data = att.get("data", "")
                if mime.startswith("image/") and isinstance(data, str) and data:
                    img_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{data}"},
                        }
                    )
                elif isinstance(att.get("text_content"), str) and att["text_content"]:
                    text_acc.append(f"\n[Attached file: {name}]\n{att['text_content'][:40000]}")

            combined_text = "\n".join(text_acc).strip()
            if img_parts:
                parts.append({"type": "text", "text": combined_text or "Describe this image."})
                parts.extend(img_parts)
                payload.append({"role": "user", "content": parts})
            else:
                payload.append({"role": "user", "content": combined_text})
        else:
            payload.append({"role": role, "content": text})

    return payload


async def stream_chat(
    *,
    provider_id: str,
    api_key: str,
    base_url: str,
    model_id: str,
    messages: list[dict[str, Any]],
    system_instruction: str = "",
) -> AsyncIterator[str]:
    """Stream text deltas from an OpenAI-compatible free endpoint via SSE."""
    if provider_id == "openrouter" and not is_openrouter_free(model_id):
        raise ProviderError(
            f"Blocked non-free OpenRouter model '{model_id}'. Free-Only mode is enforced."
        )

    spec = PROVIDER_MAP[provider_id]
    clean_url = (base_url or spec.default_base_url).strip().rstrip("/")
    headers = _headers_for(provider_id, api_key)
    body = {
        "model": model_id,
        "messages": _build_openai_messages(messages, system_instruction),
        "stream": True,
        "temperature": 0.7,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)) as client:
        try:
            async with client.stream(
                "POST",
                f"{clean_url}/chat/completions",
                headers=headers,
                json=body,
            ) as resp:
                if resp.status_code != 200:
                    err_bytes = await resp.aread()
                    raise _classify_http_error(
                        provider_id,
                        resp.status_code,
                        err_bytes.decode("utf-8", errors="replace"),
                    )

                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if "error" in chunk and chunk["error"]:
                        err_msg = str(chunk["error"])
                        if "429" in err_msg or "rate" in err_msg.lower():
                            raise RateLimitError(err_msg)
                        raise ProviderError(err_msg)

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield content
        except ProviderError:
            raise
        except httpx.ConnectError as exc:
            raise ProviderError(f"Could not connect to {provider_id} ({clean_url}): {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Network error calling {provider_id}: {exc}") from exc


async def transcribe_groq(
    *,
    api_key: str,
    audio_bytes: bytes,
    filename: str = "speech.wav",
    language: str = "auto",
) -> str:
    """Transcribe speech using Groq's free Whisper Large v3 Turbo endpoint."""
    if not api_key.strip():
        raise ProviderAuthError("Groq API key required for Whisper STT.")

    headers = {"Authorization": f"Bearer {api_key.strip()}"}
    data: dict[str, str] = {
        "model": "whisper-large-v3-turbo",
        "response_format": "json",
    }
    if language and language != "auto":
        data["language"] = language.split("-")[0].lower()

    files = {"file": (filename, audio_bytes, "audio/wav")}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers=headers,
            data=data,
            files=files,
        )
        if resp.status_code != 200:
            raise _classify_http_error("groq", resp.status_code, resp.text)
        payload = resp.json()
        return str(payload.get("text", "")).strip()
