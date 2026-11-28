"""Automated unit and integration tests for FreeAI Hub."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from backend.crypto import KeyVault
from backend.db import Database
from backend.providers.base import ModelInfo, ProviderError, RateLimitError
from backend.providers.openai_compat import is_openrouter_free, stream_chat as openai_stream_chat
from backend.router import RateLimitTracker, classify_prompt, stream_chat_with_routing
from backend.voice import clean_text_for_tts, pcm16_to_wav_bytes, select_edge_voice


@pytest.fixture()
def isolated_env(tmp_path: Path):
    vault = KeyVault(tmp_path / ".vault.key")
    db = Database(tmp_path / "test.db", vault=vault)
    tracker = RateLimitTracker()
    app = create_app(db_inst=db, tracker_inst=tracker)
    client = TestClient(app)
    return db, tracker, app, client


def test_vault_roundtrip_and_masking(tmp_path: Path) -> None:
    vault = KeyVault(tmp_path / ".vault.key")
    secret = "AIzaSyTestSecretKey1234567890"
    enc = vault.encrypt(secret)
    assert enc != secret
    assert vault.decrypt(enc) == secret
    assert KeyVault.mask_key(secret) == "AIza••••7890"
    assert vault.decrypt("not-valid-fernet") == ""


def test_sqlite_crud_bangla_utf8(isolated_env) -> None:
    db, _, _, _ = isolated_env
    conv = db.create_conversation("বাংলায় টেস্ট চ্যাট")
    assert conv["title"] == "বাংলায় টেস্ট চ্যাট"

    m1 = db.add_message(conv["id"], "user", "হ্যালো, কেমন আছেন?")
    m2 = db.add_message(
        conv["id"],
        "assistant",
        "ভালো আছি!",
        provider_id="gemini",
        model_id="gemini-3.5-flash-lite",
        route_reason="Auto · Multilingual",
    )
    assert m1["content"] == "হ্যালো, কেমন আছেন?"
    assert m2["provider_id"] == "gemini"

    msgs = db.list_messages(conv["id"])
    assert len(msgs) == 2

    results = db.list_conversations(search="কেমন আছেন")
    assert len(results) == 1 and results[0]["id"] == conv["id"]

    updated = db.update_conversation(conv["id"], title="আপডেটেড টাইটেল", pinned=True)
    assert updated is not None and updated["pinned"] is True

    assert db.delete_last_assistant_message(conv["id"]) is True
    assert len(db.list_messages(conv["id"])) == 1
    assert db.delete_conversation(conv["id"]) is True


def test_raw_key_never_exposed_in_api(isolated_env) -> None:
    db, _, _, client = isolated_env
    raw_secret = "gsk_supersecret_value_998877"
    db.save_provider(
        "groq",
        api_key=raw_secret,
        status="ok",
        status_message="Connected",
        tested=True,
    )

    boot_resp = client.get("/api/bootstrap")
    assert boot_resp.status_code == 200
    boot_text = boot_resp.text
    assert raw_secret not in boot_text
    groq_pub = next(p for p in boot_resp.json()["providers"] if p["id"] == "groq")
    assert groq_pub["has_key"] is True
    assert groq_pub["masked_key"] == "gsk_••••8877"


def test_openrouter_strict_free_only_guard() -> None:
    assert is_openrouter_free("openrouter/free") is True
    assert is_openrouter_free("deepseek/deepseek-r1:free") is True
    assert is_openrouter_free("custom/free-model", {"pricing": {"prompt": "0", "completion": "0"}}) is True
    assert is_openrouter_free("openai/gpt-4o") is False
    assert is_openrouter_free("anthropic/claude-3.5-sonnet", {"pricing": {"prompt": "0.003", "completion": "0.015"}}) is False

    async def _call_paid() -> None:
        async for _ in openai_stream_chat(
            provider_id="openrouter",
            api_key="or-test",
            base_url="https://openrouter.ai/api/v1",
            model_id="openai/gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
        ):
            pass

    import asyncio

    with pytest.raises(ProviderError, match="Free-Only mode"):
        asyncio.run(_call_paid())


def test_auto_router_classification_and_429_fallback() -> None:
    cat_v, _ = classify_prompt("What is in this photo?", [{"mime_type": "image/png", "data": "abc"}])
    assert cat_v == "vision"

    cat_c, _ = classify_prompt("Fix this Python FastAPI traceback error in def main():")
    assert cat_c == "coding"

    cat_l, _ = classify_prompt("Summarize this " + ("x" * 13000))
    assert cat_l == "long_context"

    cat_m, _ = classify_prompt("বাংলাদেশে ভ্রমণের সেরা জায়গা কোনগুলো?")
    assert cat_m == "multilingual"

    # Test 429 fallback from Groq to Gemini
    m_groq = ModelInfo(
        id="qwen/qwen3-32b",
        label="Qwen 3 32B",
        provider_id="groq",
        coding_rank=92,
        speed_rank=94,
    )
    m_gem = ModelInfo(
        id="gemini-3.5-flash-lite",
        label="Gemini 3.5 Flash-Lite",
        provider_id="gemini",
        coding_rank=84,
        speed_rank=96,
        vision=True,
    )
    connected = [
        (m_groq, {"provider_id": "groq", "api_key": "k1", "base_url": "https://api.groq.com/openai/v1"}),
        (m_gem, {"provider_id": "gemini", "api_key": "k2", "base_url": ""}),
    ]
    tracker = RateLimitTracker()

    async def fake_groq_stream(**kwargs):
        if False:
            yield ""
        raise RateLimitError("429 quota hit")

    async def fake_gemini_stream(**kwargs):
        yield "হ্যালো "
        yield "কাজ করছে!"

    async def _run_fallback():
        with (
            patch("backend.providers.openai_compat.stream_chat", side_effect=fake_groq_stream),
            patch("backend.providers.gemini_provider.stream_chat", side_effect=fake_gemini_stream),
        ):
            events = []
            async for ev in stream_chat_with_routing(
                connected,
                messages=[{"role": "user", "content": "Write Python code to sort a list"}],
                mode="auto",
                auto_fallback=True,
                tracker=tracker,
            ):
                events.append(ev)
            return events

    import asyncio

    events = asyncio.run(_run_fallback())
    meta = next(e for e in events if e["type"] == "meta")
    assert meta["provider_id"] == "gemini"
    assert meta["fallback_used"] is True
    assert "Fallback" in meta["route_reason"]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "হ্যালো কাজ করছে!"
    assert tracker.is_available("groq", "qwen/qwen3-32b") is False


def test_fastapi_routes_pwa_chat_stream_and_pin_lock(isolated_env) -> None:
    db, _, _, client = isolated_env

    # Static PWA assets
    for path in ("/", "/manifest.json", "/sw.js", "/static/app.js", "/static/voice.js", "/static/pcm-processor.js"):
        r = client.get(path)
        assert r.status_code == 200, f"Failed {path}"

    # Conversations CRUD
    c_resp = client.post("/api/conversations", json={"title": "Test Thread"})
    assert c_resp.status_code == 200
    conv_id = c_resp.json()["id"]
    assert client.patch(f"/api/conversations/{conv_id}", json={"pinned": True}).json()["pinned"] is True

    # Connect mock provider & stream SSE chat
    db.save_provider(
        "gemini",
        api_key="AIzaTest1234",
        models=[
            ModelInfo(
                id="gemini-3.5-flash-lite",
                label="Gemini 3.5 Flash-Lite",
                provider_id="gemini",
                vision=True,
                coding_rank=84,
                speed_rank=96,
            )
        ],
        status="ok",
        tested=True,
    )

    async def fake_gemini_stream(**kwargs):
        yield "নমস্কার! "
        yield "সব ঠিক আছে।"

    with patch("backend.providers.gemini_provider.stream_chat", side_effect=fake_gemini_stream):
        stream_resp = client.post(
            "/api/chat/stream",
            json={"conversation_id": conv_id, "prompt": "বাংলায় উত্তর দাও", "mode": "auto"},
        )
        assert stream_resp.status_code == 200
        events = [
            json.loads(line[5:].strip())
            for line in stream_resp.text.splitlines()
            if line.startswith("data:")
        ]

    assert any(e["type"] == "meta" and e["provider_id"] == "gemini" for e in events)
    assert any(e["type"] == "done" for e in events)
    saved_msgs = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert len(saved_msgs) == 2
    assert saved_msgs[1]["content"] == "নমস্কার! সব ঠিক আছে।"

    # PIN lock enforcement
    pin_resp = client.patch("/api/settings", json={"access_pin": "2468"})
    assert pin_resp.status_code == 200
    assert pin_resp.json()["pin_enabled"] is True

    assert client.get("/api/bootstrap").status_code == 401
    assert client.get("/api/bootstrap", headers={"X-Access-Pin": "0000"}).status_code == 401
    assert client.get("/api/bootstrap", headers={"X-Access-Pin": "2468"}).status_code == 200

    # Clear PIN
    clear_resp = client.patch("/api/settings", json={"access_pin": ""}, headers={"X-Access-Pin": "2468"})
    assert clear_resp.json()["pin_enabled"] is False
    assert client.get("/api/bootstrap").status_code == 200


def test_voice_helpers_and_universal_websocket(isolated_env) -> None:
    db, _, _, client = isolated_env

    assert select_edge_voice("আমি বাংলায় কথা বলছি", "auto", "female") == "bn-BD-NabanitaNeural"
    assert select_edge_voice("আমি বাংলায় কথা বলছি", "auto", "male") == "bn-BD-PradeepNeural"
    assert select_edge_voice("Hello world", "auto", "female") == "en-US-AriaNeural"
    assert select_edge_voice("नमस्ते", "auto", "female") == "hi-IN-SwaraNeural"
    assert "```" not in clean_text_for_tts("দেখুন:\n```python\nprint(1)\n```")

    wav = pcm16_to_wav_bytes(b"\x00\x00" * 1600, sample_rate=16000)
    assert wav[:4] == b"RIFF"

    db.save_provider(
        "groq",
        api_key="gsk_test_1234",
        models=[
            ModelInfo(
                id="llama-3.3-70b-versatile",
                label="Llama 3.3 70B",
                provider_id="groq",
                coding_rank=86,
                speed_rank=95,
            )
        ],
        status="ok",
        tested=True,
    )

    async def fake_groq_stream(**kwargs):
        yield "হ্যাঁ, আমি প্রস্তুত।"

    with (
        patch("backend.providers.openai_compat.stream_chat", side_effect=fake_groq_stream),
        patch("backend.voice.synthesize_edge_tts", new=AsyncMock(return_value=(b"FAKE_MP3", "bn-BD-NabanitaNeural"))),
    ):
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_text(json.dumps({"engine": "universal", "language": "auto", "gender": "female"}))
            ready = json.loads(ws.receive_text())
            assert ready["type"] == "session_ready"
            assert ready["engine"] == "universal"

            ws.send_text(json.dumps({"type": "text", "text": "কেমন আছো?"}))
            received: list[dict[str, Any]] = []
            for _ in range(10):
                ev = json.loads(ws.receive_text())
                received.append(ev)
                if ev["type"] == "turn_complete":
                    break

            assert any(e["type"] == "audio_mp3" and e["voice"] == "bn-BD-NabanitaNeural" for e in received)
            assert any(e["type"] == "turn_complete" for e in received)
            ws.send_text(json.dumps({"type": "stop"}))


def test_live_voice_regression_fixes() -> None:
    voice_js = (ROOT / "frontend" / "static" / "voice.js").read_text(encoding="utf-8")
    assert "voiceState.orb" not in voice_js
    assert "vEls.orb.classList.contains('listening')" in voice_js
    assert "vEls.orb.style.transform" in voice_js

    voice_py = (ROOT / "backend" / "voice.py").read_text(encoding="utf-8")
    assert '"api_version": "v1beta"' in voice_py
    assert "while not stop_event.is_set():" in voice_py
    assert "async for response in session.receive():" in voice_py


def test_non_chat_model_filters() -> None:
    from types import SimpleNamespace
    from backend.providers.gemini_provider import _merge_discovered_models

    fake_api_models = [
        SimpleNamespace(name="models/gemini-3.5-flash-lite", display_name="Gemini 3.5 Flash-Lite", supported_actions=["generateContent"]),
        SimpleNamespace(name="models/gemini-nano-banana-2.1", display_name="Nano Banana 2.1", supported_actions=["generateContent"]),
    ]
    merged_ids = [m.id for m in _merge_discovered_models(fake_api_models)]
    assert "gemini-3.5-flash-lite" in merged_ids
    assert "gemini-nano-banana-2.1" not in merged_ids

    compat_py = (ROOT / "backend" / "providers" / "openai_compat.py").read_text(encoding="utf-8")
    assert '"transcribe"' in compat_py


def test_seed_from_env_preserves_existing_keys_and_sets_pin(isolated_env) -> None:
    db, _, _, client = isolated_env
    db.save_provider("groq", api_key="gsk_existing_ui_key", status="ok", tested=True)

    seeded = db.seed_from_env(
        {
            "GEMINI_API_KEY": "AIzaSyEnvSeededSecret9999",
            "GROQ_API_KEY": "gsk_should_not_overwrite",
            "FREEAI_ACCESS_PIN": "4321",
        }
    )
    assert "gemini" in seeded
    assert "groq" not in seeded
    assert "access_pin" in seeded

    assert db.get_provider_secret("gemini")["api_key"] == "AIzaSyEnvSeededSecret9999"
    assert db.get_provider_secret("groq")["api_key"] == "gsk_existing_ui_key"
    assert db.is_pin_enabled() is True
    assert db.verify_pin("4321") is True

    # Ensure raw secret is never exposed even when seeded from env
    resp = client.get("/api/bootstrap", headers={"X-Access-Pin": "4321"})
    assert resp.status_code == 200
    assert "AIzaSyEnvSeededSecret9999" not in resp.text


def test_background_memory_extraction_injection_and_sync_restore(isolated_env) -> None:
    db, _, _, client = isolated_env

    # HEAD health checks should return 200 (no 405 noise on Render)
    assert client.head("/").status_code == 200
    assert client.head("/api/health").status_code == 200

    # Manual memory CRUD
    m_resp = client.post("/api/memories", json={"content": "আমার নাম হাবিব", "category": "manual"})
    assert m_resp.status_code == 200
    mem_id = m_resp.json()["id"]
    assert len(client.get("/api/memories").json()) == 1

    # Connect mock provider and verify automatic Bengali memory extraction + system prompt injection
    db.save_provider(
        "gemini",
        api_key="AIzaTestMem",
        models=[
            ModelInfo(
                id="gemini-3.5-flash-lite",
                label="Gemini 3.5 Flash-Lite",
                provider_id="gemini",
                coding_rank=84,
                speed_rank=96,
            )
        ],
        status="ok",
        tested=True,
    )
    captured_sys: list[str] = []

    async def fake_stream(**kwargs):
        captured_sys.append(kwargs.get("system_instruction", ""))
        yield "মনে রাখলাম!"

    with patch("backend.providers.gemini_provider.stream_chat", side_effect=fake_stream):
        r = client.post(
            "/api/chat/stream",
            json={
                "prompt": "মনে রাখো আমি পাইথন ও FastAPI নিয়ে কাজ করি",
                "mode": "auto",
                "auto_hint": "coding",
            },
        )
        assert r.status_code == 200
        assert "memory_saved" in r.text
        assert "Auto · Coding" in r.text

    assert captured_sys and "আমার নাম হাবিব" in captured_sys[0]
    assert "পাইথন ও FastAPI" in captured_sys[0]

    # Auto-hint classification check
    assert classify_prompt("hi", auto_hint="fast") == ("fast", "Auto · Ultra-Fast")
    assert classify_prompt("hi", auto_hint="coding") == ("coding", "Auto · Coding")
    assert classify_prompt("hi", auto_hint="multilingual") == ("multilingual", "Auto · বাংলা / Multilingual")

    # Delete memory
    assert client.delete(f"/api/memories/{mem_id}").status_code == 200

    # Backup sync/restore idempotency (simulating Render ephemeral disk restart)
    restore_resp = client.post(
        "/api/sync/restore",
        json={
            "conversations": [{"id": "conv_old_1", "title": "পুরনো চ্যাট", "pinned": True}],
            "messages_by_conv": {
                "conv_old_1": [
                    {"id": "m1", "role": "user", "content": "হ্যালো"},
                    {"id": "m2", "role": "assistant", "content": "নমস্কার!"},
                ]
            },
            "memories": [{"id": "mem_old_1", "content": "সবসময় বাংলায় উত্তর দেবে", "category": "preference"}],
        },
    )
    assert restore_resp.status_code == 200
    body = restore_resp.json()
    assert body["restored"]["conversations"] == 1
    assert body["restored"]["messages"] == 2
    assert body["restored"]["memories"] == 1
    assert len(client.get("/api/conversations/conv_old_1/messages").json()) == 2

    # Second restore call must be idempotent (0 duplicates)
    restore_again = client.post(
        "/api/sync/restore",
        json={
            "conversations": [{"id": "conv_old_1", "title": "পুরনো চ্যাট", "pinned": True}],
            "messages_by_conv": {"conv_old_1": [{"id": "m1", "role": "user", "content": "হ্যালো"}]},
            "memories": [{"id": "mem_old_1", "content": "সবসময় বাংলায় উত্তর দেবে", "category": "preference"}],
        },
    )
    assert restore_again.json()["restored"] == {"conversations": 0, "messages": 0, "memories": 0}
