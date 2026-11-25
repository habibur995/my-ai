"""Smart Auto-Model Router and HTTP 429 Rate-Limit Fallback Engine."""

from __future__ import annotations

import re
import threading
import time
from typing import Any, AsyncIterator

from backend.providers import gemini_provider, openai_compat
from backend.providers.base import (
    ModelInfo,
    ProviderAuthError,
    ProviderError,
    RateLimitError,
)

_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")
_CODE_KEYWORDS = (
    "```",
    "def ",
    "class ",
    "function ",
    "const ",
    "let ",
    "import ",
    "from ",
    "select ",
    "create table",
    "traceback",
    "exception",
    "stack trace",
    "debug",
    "refactor",
    "python",
    "javascript",
    "typescript",
    "fastapi",
    "react",
    "html",
    "css",
    "sql",
    "regex",
    "কোড",
    "বাগ",
    "এরর",
    "প্রোগ্রাম",
    "স্ক্রিপ্ট",
    "ফাংশন",
)


class RateLimitTracker:
    """Tracks temporary per-model cooldowns after 429 or transient errors."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cooldowns: dict[tuple[str, str], tuple[float, str]] = {}

    def mark_cooldown(
        self,
        provider_id: str,
        model_id: str,
        seconds: float = 60.0,
        reason: str = "Rate limit (429)",
    ) -> None:
        until = time.monotonic() + max(1.0, seconds)
        with self._lock:
            self._cooldowns[(provider_id, model_id)] = (until, reason)

    def clear(self, provider_id: str | None = None) -> None:
        with self._lock:
            if provider_id is None:
                self._cooldowns.clear()
            else:
                for key in [k for k in self._cooldowns if k[0] == provider_id]:
                    del self._cooldowns[key]

    def remaining_seconds(self, provider_id: str, model_id: str) -> int:
        now = time.monotonic()
        with self._lock:
            entry = self._cooldowns.get((provider_id, model_id))
            if not entry:
                return 0
            until, _ = entry
            if until <= now:
                del self._cooldowns[(provider_id, model_id)]
                return 0
            return int(until - now) + 1

    def is_available(self, provider_id: str, model_id: str) -> bool:
        return self.remaining_seconds(provider_id, model_id) == 0

    def get_health_summary(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        out: list[dict[str, Any]] = []
        with self._lock:
            expired = [k for k, (until, _) in self._cooldowns.items() if until <= now]
            for k in expired:
                del self._cooldowns[k]
            for (pid, mid), (until, reason) in self._cooldowns.items():
                out.append(
                    {
                        "provider_id": pid,
                        "model_id": mid,
                        "cooldown_seconds": int(until - now) + 1,
                        "reason": reason,
                    }
                )
        return out


rate_tracker = RateLimitTracker()


def classify_prompt(
    prompt: str,
    attachments: list[dict[str, Any]] | None = None,
    history_chars: int = 0,
) -> tuple[str, str]:
    """Classify the user turn into a routing category and human-readable badge label."""
    atts = attachments or []
    has_image = any(str(a.get("mime_type", "")).startswith("image/") for a in atts)
    if has_image:
        return "vision", "Auto · Vision"

    has_text_file = any(
        bool(a.get("text_content")) or str(a.get("mime_type", "")).startswith("text/")
        for a in atts
    )
    total_chars = history_chars + len(prompt or "") + sum(
        len(str(a.get("text_content", ""))) for a in atts
    )

    lower = (prompt or "").lower()
    if any(kw in lower for kw in _CODE_KEYWORDS):
        return "coding", "Auto · Coding"

    if has_text_file or total_chars > 12_000:
        return "long_context", "Auto · Long Context"

    if _NON_ASCII_RE.search(prompt or "") or any(
        w in lower for w in ("translate", "bangla", "bengali", "hindi", "arabic", "অনুবাদ", "বাংলা")
    ):
        return "multilingual", "Auto · Multilingual"

    return "general", "Auto · Smart Fast"


def _score_model(model: ModelInfo, category: str) -> float:
    provider_bonus = {
        "gemini": 14.0,
        "groq": 12.0,
        "openrouter": 6.0,
        "nvidia": 5.0,
        "cohere": 4.0,
        "huggingface": 2.0,
        "ollama": 8.0,
    }.get(model.provider_id, 0.0)

    if category == "vision":
        if not model.vision:
            return -10_000.0
        return 1000.0 + model.speed_rank * 0.8 + model.coding_rank * 0.8 + provider_bonus

    if category == "coding":
        return model.coding_rank * 2.0 + model.speed_rank * 0.4 + provider_bonus

    if category == "long_context":
        ctx_score = min(model.context_k, 1000) * 0.12
        return ctx_score + model.coding_rank * 1.1 + model.speed_rank * 0.3 + provider_bonus

    if category == "multilingual":
        multi_bonus = 18.0 if model.provider_id in ("gemini", "groq", "cohere") else 4.0
        return model.speed_rank * 1.05 + model.coding_rank * 0.95 + multi_bonus

    # general
    return model.speed_rank * 1.3 + model.coding_rank * 0.7 + provider_bonus


def build_candidate_chain(
    connected_items: list[tuple[ModelInfo, dict[str, Any]]],
    *,
    mode: str = "auto",
    selected_provider: str = "",
    selected_model: str = "",
    category: str = "general",
    auto_fallback: bool = True,
    tracker: RateLimitTracker | None = None,
) -> list[tuple[ModelInfo, dict[str, Any]]]:
    """Order connected free models into a prioritized primary + fallback execution chain."""
    active_tracker = tracker or rate_tracker
    if not connected_items:
        return []

    filtered = list(connected_items)
    if category == "vision":
        vision_only = [(m, s) for m, s in filtered if m.vision]
        if not vision_only:
            raise ProviderError(
                "No vision-capable free model is connected. Connect Google Gemini or Groq (Llama 4 Scout) to analyze images."
            )
        filtered = vision_only

    if mode == "manual" and selected_provider and selected_model:
        primary: list[tuple[ModelInfo, dict[str, Any]]] = []
        backups: list[tuple[ModelInfo, dict[str, Any]]] = []
        for item in filtered:
            m, _ = item
            if m.provider_id == selected_provider and m.id == selected_model:
                primary.append(item)
            else:
                backups.append(item)

        if not primary:
            # User selected a specific model not currently in cached list; synthesize entry if provider exists
            matching_secret = next(
                (s for _, s in connected_items if s["provider_id"] == selected_provider),
                None,
            )
            if matching_secret:
                primary.append(
                    (
                        ModelInfo(
                            id=selected_model,
                            label=selected_model,
                            provider_id=selected_provider,
                            vision=(category == "vision"),
                            free_verified=True,
                        ),
                        matching_secret,
                    )
                )

        if not auto_fallback:
            return primary

        backups.sort(
            key=lambda it: (
                0 if active_tracker.is_available(it[0].provider_id, it[0].id) else 1,
                -_score_model(it[0], category),
            )
        )
        return primary + backups

    # Auto mode: rank all models by category score, healthy models first
    filtered.sort(
        key=lambda it: (
            0 if active_tracker.is_available(it[0].provider_id, it[0].id) else 1,
            active_tracker.remaining_seconds(it[0].provider_id, it[0].id),
            -_score_model(it[0], category),
        )
    )
    return filtered


async def _stream_single_model(
    model: ModelInfo,
    secret: dict[str, Any],
    messages: list[dict[str, Any]],
    system_instruction: str,
) -> AsyncIterator[str]:
    pid = model.provider_id
    if pid == "gemini":
        async for chunk in gemini_provider.stream_chat(
            api_key=secret["api_key"],
            model_id=model.id,
            messages=messages,
            system_instruction=system_instruction,
        ):
            yield chunk
    else:
        async for chunk in openai_compat.stream_chat(
            provider_id=pid,
            api_key=secret["api_key"],
            base_url=secret["base_url"],
            model_id=model.id,
            messages=messages,
            system_instruction=system_instruction,
        ):
            yield chunk


async def stream_chat_with_routing(
    connected_items: list[tuple[ModelInfo, dict[str, Any]]],
    *,
    messages: list[dict[str, Any]],
    system_instruction: str = "",
    mode: str = "auto",
    selected_provider: str = "",
    selected_model: str = "",
    auto_fallback: bool = True,
    tracker: RateLimitTracker | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Select the optimal free model and stream tokens with automatic 429 fallback."""
    active_tracker = tracker or rate_tracker
    last_user_msg = next((m for m in reversed(messages) if m.get("role") == "user"), {})
    prompt_text = str(last_user_msg.get("content", "") or "")
    atts = last_user_msg.get("attachments") or []
    history_chars = sum(len(str(m.get("content", "") or "")) for m in messages[:-1])

    category, category_label = classify_prompt(prompt_text, atts, history_chars)
    candidates = build_candidate_chain(
        connected_items,
        mode=mode,
        selected_provider=selected_provider,
        selected_model=selected_model,
        category=category,
        auto_fallback=auto_fallback,
        tracker=active_tracker,
    )

    if not candidates:
        raise ProviderError(
            "No free AI provider is connected yet. Open Settings and add at least one free API key (such as Google Gemini, Groq, or OpenRouter) or connect local Ollama."
        )

    errors: list[str] = []
    fallback_note = ""

    for idx, (model, secret) in enumerate(candidates):
        emitted_any = False
        try:
            route_reason = (
                category_label if mode == "auto" else "Manual"
            )
            if fallback_note:
                route_reason = f"{route_reason} · {fallback_note}"

            async for delta in _stream_single_model(model, secret, messages, system_instruction):
                if not emitted_any:
                    emitted_any = True
                    yield {
                        "type": "meta",
                        "provider_id": model.provider_id,
                        "model_id": model.id,
                        "model_label": model.label,
                        "route_reason": route_reason,
                        "category": category,
                        "fallback_used": bool(fallback_note),
                    }
                yield {"type": "delta", "text": delta}

            if not emitted_any:
                yield {
                    "type": "meta",
                    "provider_id": model.provider_id,
                    "model_id": model.id,
                    "model_label": model.label,
                    "route_reason": route_reason,
                    "category": category,
                    "fallback_used": bool(fallback_note),
                }
            yield {"type": "done"}
            return
        except RateLimitError as exc:
            active_tracker.mark_cooldown(model.provider_id, model.id, seconds=60.0, reason=str(exc))
            errors.append(f"{model.provider_id}/{model.id}: {exc}")
            if emitted_any or not auto_fallback or idx == len(candidates) - 1:
                break
            fallback_note = f"Fallback from {model.label} (429)"
        except ProviderAuthError as exc:
            active_tracker.mark_cooldown(model.provider_id, model.id, seconds=300.0, reason=str(exc))
            errors.append(f"{model.provider_id}/{model.id}: {exc}")
            if emitted_any or not auto_fallback or idx == len(candidates) - 1:
                break
            fallback_note = f"Fallback from {model.label} (Auth)"
        except ProviderError as exc:
            active_tracker.mark_cooldown(model.provider_id, model.id, seconds=20.0, reason=str(exc))
            errors.append(f"{model.provider_id}/{model.id}: {exc}")
            if emitted_any or not auto_fallback or idx == len(candidates) - 1:
                break
            fallback_note = f"Fallback from {model.label}"

    raise ProviderError("All tried free models failed:\n" + "\n".join(errors[:4]))
