"""FreeAI Hub — FastAPI server (HTTP + SSE + WebSocket + Responsive PWA)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import DEFAULT_PORT, FRONTEND_DIR, get_lan_ips
from backend.db import Database, db as default_db
from backend.memory import build_system_instruction, extract_memories_fast
from backend.providers import gemini_provider, openai_compat
from backend.providers.base import ProviderError
from backend.providers.catalog import PROVIDER_MAP
from backend.router import RateLimitTracker, rate_tracker as default_tracker, stream_chat_with_routing
from backend.voice import handle_voice_websocket, synthesize_edge_tts


class ProviderUpdateBody(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    enabled: bool | None = None
    test: bool = True


class ConversationCreateBody(BaseModel):
    title: str = "New Chat"


class ConversationUpdateBody(BaseModel):
    title: str | None = None
    pinned: bool | None = None


class ChatStreamBody(BaseModel):
    conversation_id: str = ""
    prompt: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    mode: str = "auto"
    auto_hint: str = ""
    provider_id: str = ""
    model_id: str = ""
    regenerate: bool = False


class CompareChatBody(BaseModel):
    conversation_id: str = ""
    prompt: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    slot_a: dict[str, str]
    slot_b: dict[str, str]


class PinVerifyBody(BaseModel):
    pin: str = ""


class TtsRequestBody(BaseModel):
    text: str
    language: str = "auto"
    gender: str = "female"


class MemoryCreateBody(BaseModel):
    content: str
    category: str = "manual"


class SyncRestoreBody(BaseModel):
    conversations: list[dict[str, Any]] = Field(default_factory=list)
    messages_by_conv: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    memories: list[dict[str, Any]] = Field(default_factory=list)


def _sse(event_data: dict[str, Any]) -> str:
    return f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"


def create_app(
    db_inst: Database | None = None,
    tracker_inst: RateLimitTracker | None = None,
) -> FastAPI:
    db = db_inst or default_db
    tracker = tracker_inst or default_tracker

    app = FastAPI(title="FreeAI Hub", version="1.0.0")

    def require_pin(request: Request) -> None:
        if not db.is_pin_enabled():
            return
        pin = request.headers.get("X-Access-Pin") or request.query_params.get("pin") or ""
        if not db.verify_pin(pin):
            raise HTTPException(status_code=401, detail="Invalid or missing access PIN")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.head("/", include_in_schema=False)
    async def index_head() -> Response:
        return Response(status_code=200)

    @app.get("/manifest.json", include_in_schema=False)
    async def manifest() -> FileResponse:
        return FileResponse(
            FRONTEND_DIR / "manifest.json",
            media_type="application/manifest+json",
        )

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        return FileResponse(
            FRONTEND_DIR / "sw.js",
            media_type="application/javascript",
        )

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True}

    @app.head("/api/health", include_in_schema=False)
    async def health_head() -> Response:
        return Response(status_code=200)

    @app.get("/api/auth/status")
    async def auth_status() -> dict[str, Any]:
        return {"pin_enabled": db.is_pin_enabled()}

    @app.post("/api/auth/verify")
    async def auth_verify(body: PinVerifyBody) -> dict[str, Any]:
        valid = db.verify_pin(body.pin)
        if not valid:
            raise HTTPException(status_code=401, detail="Incorrect PIN")
        return {"valid": True}

    @app.get("/api/bootstrap", dependencies=[Depends(require_pin)])
    async def bootstrap() -> dict[str, Any]:
        return {
            "settings": db.get_settings_public(),
            "providers": db.list_providers_public(),
            "conversations": db.list_conversations(),
            "memories": db.list_memories(),
            "rate_health": tracker.get_health_summary(),
            "network": {
                "port": DEFAULT_PORT,
                "lan_ips": get_lan_ips(),
            },
        }

    @app.get("/api/providers", dependencies=[Depends(require_pin)])
    async def list_providers() -> list[dict[str, Any]]:
        return db.list_providers_public()

    @app.post("/api/providers/{provider_id}", dependencies=[Depends(require_pin)])
    async def save_provider(provider_id: str, body: ProviderUpdateBody) -> dict[str, Any]:
        spec = PROVIDER_MAP.get(provider_id)
        if not spec:
            raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")

        existing = db.get_provider_secret(provider_id)
        effective_key = (
            body.api_key.strip()
            if body.api_key is not None
            else (existing["api_key"] if existing else "")
        )
        effective_url = (
            body.base_url.strip()
            if body.base_url is not None
            else (existing["base_url"] if existing else spec.default_base_url)
        ) or spec.default_base_url

        # Enable-only toggle without changing key or URL
        if body.api_key is None and body.base_url is None and body.enabled is not None and not body.test:
            updated = db.save_provider(provider_id, enabled=body.enabled)
            return {"ok": True, "message": "Updated", "provider": updated}

        if not body.test:
            updated = db.save_provider(
                provider_id,
                api_key=body.api_key,
                base_url=effective_url,
                enabled=body.enabled,
            )
            return {"ok": True, "message": "Saved", "provider": updated}

        if spec.adapter == "gemini":
            ok, msg, models = await gemini_provider.test_and_discover(effective_key)
        else:
            ok, msg, models = await openai_compat.test_and_discover(
                provider_id, effective_key, effective_url
            )

        tracker.clear(provider_id)
        status = "ok" if ok else ("auth_error" if "key" in msg.lower() or "401" in msg else "error")
        updated = db.save_provider(
            provider_id,
            api_key=body.api_key,
            base_url=effective_url,
            enabled=body.enabled if body.enabled is not None else True,
            models=models if ok else None,
            status=status,
            status_message=msg,
            tested=True,
        )
        return {"ok": ok, "message": msg, "provider": updated}

    @app.post("/api/providers/{provider_id}/refresh", dependencies=[Depends(require_pin)])
    async def refresh_provider(provider_id: str) -> dict[str, Any]:
        spec = PROVIDER_MAP.get(provider_id)
        if not spec:
            raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
        secret = db.get_provider_secret(provider_id)
        api_key = secret["api_key"] if secret else ""
        base_url = (secret["base_url"] if secret else spec.default_base_url) or spec.default_base_url

        if spec.adapter == "gemini":
            ok, msg, models = await gemini_provider.test_and_discover(api_key)
        else:
            ok, msg, models = await openai_compat.test_and_discover(provider_id, api_key, base_url)

        tracker.clear(provider_id)
        updated = db.save_provider(
            provider_id,
            models=models if ok else None,
            status="ok" if ok else "error",
            status_message=msg,
            tested=True,
        )
        return {"ok": ok, "message": msg, "provider": updated}

    @app.delete("/api/providers/{provider_id}", dependencies=[Depends(require_pin)])
    async def delete_provider(provider_id: str) -> dict[str, Any]:
        if provider_id not in PROVIDER_MAP:
            raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_id}")
        db.delete_provider(provider_id)
        tracker.clear(provider_id)
        return {"ok": True, "provider": db.get_provider_public(provider_id)}

    @app.get("/api/conversations", dependencies=[Depends(require_pin)])
    async def list_conversations(q: str = "") -> list[dict[str, Any]]:
        return db.list_conversations(search=q)

    @app.post("/api/conversations", dependencies=[Depends(require_pin)])
    async def create_conversation(body: ConversationCreateBody) -> dict[str, Any]:
        return db.create_conversation(title=body.title)

    @app.patch("/api/conversations/{conv_id}", dependencies=[Depends(require_pin)])
    async def update_conversation(conv_id: str, body: ConversationUpdateBody) -> dict[str, Any]:
        updated = db.update_conversation(conv_id, title=body.title, pinned=body.pinned)
        if not updated:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return updated

    @app.delete("/api/conversations/{conv_id}", dependencies=[Depends(require_pin)])
    async def delete_conversation(conv_id: str) -> dict[str, Any]:
        if not db.delete_conversation(conv_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"ok": True}

    @app.get("/api/conversations/{conv_id}/messages", dependencies=[Depends(require_pin)])
    async def get_messages(conv_id: str) -> list[dict[str, Any]]:
        conv = db.get_conversation(conv_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return db.list_messages(conv_id)

    @app.get("/api/settings", dependencies=[Depends(require_pin)])
    async def get_settings() -> dict[str, Any]:
        return db.get_settings_public()

    @app.patch("/api/settings", dependencies=[Depends(require_pin)])
    async def patch_settings(request: Request) -> dict[str, Any]:
        updates = await request.json()
        if not isinstance(updates, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON object")
        return db.update_settings(updates)

    @app.get("/api/memories", dependencies=[Depends(require_pin)])
    async def list_memories() -> list[dict[str, Any]]:
        return db.list_memories()

    @app.post("/api/memories", dependencies=[Depends(require_pin)])
    async def create_memory(body: MemoryCreateBody) -> dict[str, Any]:
        if not body.content.strip():
            raise HTTPException(status_code=400, detail="Memory content is required")
        return db.add_memory(body.content, category=body.category)

    @app.delete("/api/memories/{mem_id}", dependencies=[Depends(require_pin)])
    async def delete_memory(mem_id: str) -> dict[str, Any]:
        if not db.delete_memory(mem_id):
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"ok": True}

    @app.delete("/api/memories", dependencies=[Depends(require_pin)])
    async def clear_memories() -> dict[str, Any]:
        count = db.clear_memories()
        return {"ok": True, "deleted": count}

    @app.post("/api/sync/restore", dependencies=[Depends(require_pin)])
    async def sync_restore(body: SyncRestoreBody) -> dict[str, Any]:
        counts = db.restore_backup(
            conversations=body.conversations,
            messages_by_conv=body.messages_by_conv,
            memories=body.memories,
        )
        return {
            "ok": True,
            "restored": counts,
            "conversations": db.list_conversations(),
            "memories": db.list_memories(),
        }

    @app.post("/api/tts", dependencies=[Depends(require_pin)])
    async def tts_endpoint(body: TtsRequestBody) -> Response:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="Text is required")
        mp3_bytes, _ = await synthesize_edge_tts(
            body.text,
            language=body.language,
            gender=body.gender,
        )
        return Response(content=mp3_bytes, media_type="audio/mpeg")

    @app.post("/api/chat/stream", dependencies=[Depends(require_pin)])
    async def chat_stream(body: ChatStreamBody) -> StreamingResponse:
        conv_id = body.conversation_id.strip()
        conv = db.get_conversation(conv_id) if conv_id else None
        if not conv:
            initial_title = (body.prompt or "Image / File Chat").strip().replace("\n", " ")[:48] or "New Chat"
            conv = db.create_conversation(title=initial_title)
            conv_id = conv["id"]

        if body.regenerate:
            db.delete_last_assistant_message(conv_id)
            history = db.list_messages(conv_id)
            if not history or history[-1]["role"] != "user":
                raise HTTPException(status_code=400, detail="No user message to regenerate from")
        else:
            if not body.prompt.strip() and not body.attachments:
                raise HTTPException(status_code=400, detail="Prompt or attachment is required")
            existing_msgs = db.list_messages(conv_id)
            if not existing_msgs and conv["title"] == "New Chat":
                new_title = (body.prompt or (body.attachments[0].get("name", "Chat") if body.attachments else "Chat")).strip().replace("\n", " ")[:48]
                conv = db.update_conversation(conv_id, title=new_title) or conv

            user_msg = db.add_message(
                conv_id,
                "user",
                body.prompt,
                attachments=body.attachments,
            )
            history = existing_msgs + [
                {
                    **user_msg,
                    "attachments": body.attachments,  # Keep full attachments for current turn
                }
            ]

        settings = db.get_settings_raw()
        auto_fallback = settings.get("auto_fallback", "true").lower() == "true"
        memory_enabled = settings.get("memory_enabled", "true").lower() == "true"

        saved_memories: list[dict[str, Any]] = []
        if memory_enabled and not body.regenerate and body.prompt.strip():
            for mem_text, mem_cat in extract_memories_fast(body.prompt):
                try:
                    saved_memories.append(db.add_memory(mem_text, category=mem_cat))
                except ValueError:
                    pass

        system_prompt = build_system_instruction(
            settings.get("system_prompt", ""),
            db.list_memories(),
            memory_enabled=memory_enabled,
        )
        connected_items = db.get_connected_models()

        async def event_gen():
            yield _sse({"type": "conversation", "conversation": conv})
            if saved_memories:
                yield _sse({"type": "memory_saved", "memories": saved_memories})
            chunks: list[str] = []
            meta_info: dict[str, Any] = {}
            try:
                async for ev in stream_chat_with_routing(
                    connected_items,
                    messages=history[-24:],
                    system_instruction=system_prompt,
                    mode=body.mode or "auto",
                    auto_hint=body.auto_hint or "",
                    selected_provider=body.provider_id,
                    selected_model=body.model_id,
                    auto_fallback=auto_fallback,
                    tracker=tracker,
                ):
                    if ev["type"] == "meta":
                        meta_info = ev
                        yield _sse(ev)
                    elif ev["type"] == "delta":
                        chunks.append(ev["text"])
                        yield _sse(ev)
                    elif ev["type"] == "done":
                        full_text = "".join(chunks)
                        saved_msg = db.add_message(
                            conv_id,
                            "assistant",
                            full_text,
                            provider_id=meta_info.get("provider_id", ""),
                            model_id=meta_info.get("model_id", ""),
                            route_reason=meta_info.get("route_reason", ""),
                        )
                        yield _sse(
                            {
                                "type": "done",
                                "message": saved_msg,
                                "rate_health": tracker.get_health_summary(),
                            }
                        )
            except ProviderError as exc:
                yield _sse({"type": "error", "message": str(exc)})
            except Exception as exc:
                yield _sse({"type": "error", "message": f"Unexpected server error: {exc}"})

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/chat/compare", dependencies=[Depends(require_pin)])
    async def chat_compare(body: CompareChatBody) -> StreamingResponse:
        if not body.prompt.strip() and not body.attachments:
            raise HTTPException(status_code=400, detail="Prompt or attachment is required")

        conv_id = body.conversation_id.strip()
        conv = db.get_conversation(conv_id) if conv_id else None
        if not conv:
            title = (body.prompt or "Compare Chat").strip().replace("\n", " ")[:48] or "Compare Chat"
            conv = db.create_conversation(title=title)
            conv_id = conv["id"]

        existing_msgs = db.list_messages(conv_id)
        if not existing_msgs and conv["title"] == "New Chat":
            title = (body.prompt or "Compare Chat").strip().replace("\n", " ")[:48]
            conv = db.update_conversation(conv_id, title=title) or conv

        user_msg = db.add_message(
            conv_id,
            "user",
            body.prompt,
            attachments=body.attachments,
            route_reason="Compare Mode",
        )
        history = existing_msgs + [{**user_msg, "attachments": body.attachments}]

        settings = db.get_settings_raw()
        memory_enabled = settings.get("memory_enabled", "true").lower() == "true"
        if memory_enabled and body.prompt.strip():
            for mem_text, mem_cat in extract_memories_fast(body.prompt):
                try:
                    db.add_memory(mem_text, category=mem_cat)
                except ValueError:
                    pass
        system_prompt = build_system_instruction(
            settings.get("system_prompt", ""),
            db.list_memories(),
            memory_enabled=memory_enabled,
        )
        connected_items = db.get_connected_models()

        async def event_gen():
            yield _sse({"type": "conversation", "conversation": conv})
            out_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            slot_texts: dict[str, list[str]] = {"A": [], "B": []}
            slot_meta: dict[str, dict[str, Any]] = {}

            async def run_slot(slot_name: str, slot_cfg: dict[str, str]) -> None:
                pid = str(slot_cfg.get("provider_id", ""))
                mid = str(slot_cfg.get("model_id", ""))
                try:
                    async for ev in stream_chat_with_routing(
                        connected_items,
                        messages=history[-20:],
                        system_instruction=system_prompt,
                        mode="manual",
                        selected_provider=pid,
                        selected_model=mid,
                        auto_fallback=False,
                        tracker=tracker,
                    ):
                        if ev["type"] == "meta":
                            slot_meta[slot_name] = ev
                            await out_queue.put({**ev, "slot": slot_name})
                        elif ev["type"] == "delta":
                            slot_texts[slot_name].append(ev["text"])
                            await out_queue.put({**ev, "slot": slot_name})
                        elif ev["type"] == "done":
                            await out_queue.put({"type": "slot_done", "slot": slot_name})
                except Exception as exc:
                    await out_queue.put(
                        {"type": "slot_error", "slot": slot_name, "message": str(exc)}
                    )

            async def coordinator() -> None:
                await asyncio.gather(
                    run_slot("A", body.slot_a),
                    run_slot("B", body.slot_b),
                )
                await out_queue.put(None)

            task = asyncio.create_task(coordinator())
            try:
                while True:
                    item = await out_queue.get()
                    if item is None:
                        break
                    yield _sse(item)

                # Persist comparison results cleanly into DB
                saved_msgs = []
                for s_name in ("A", "B"):
                    txt = "".join(slot_texts[s_name]).strip()
                    if txt:
                        m_info = slot_meta.get(s_name, {})
                        saved = db.add_message(
                            conv_id,
                            "assistant",
                            txt,
                            provider_id=m_info.get("provider_id", ""),
                            model_id=m_info.get("model_id", ""),
                            route_reason=f"Compare {s_name}",
                        )
                        saved_msgs.append(saved)
                yield _sse({"type": "done", "messages": saved_msgs})
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.websocket("/ws/voice")
    async def voice_ws(ws: WebSocket) -> None:
        if db.is_pin_enabled():
            pin = ws.query_params.get("pin", "")
            if not db.verify_pin(pin):
                await ws.close(code=4401)
                return
        await handle_voice_websocket(ws, db)

    @app.exception_handler(HTTPException)
    async def http_exc_handler(_req: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app


app = create_app()
