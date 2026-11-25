"""Thread-safe SQLite database layer for FreeAI Hub."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import DB_PATH, DEFAULT_SETTINGS, ensure_data_dir
from backend.crypto import KeyVault
from backend.providers.base import ModelInfo
from backend.providers.catalog import CURATED_MODELS, PROVIDER_MAP, PROVIDER_SPECS


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Manages encrypted API keys, conversations, messages, and app settings in SQLite."""

    def __init__(self, db_path: Path = DB_PATH, vault: KeyVault | None = None) -> None:
        self.db_path = Path(db_path)
        ensure_data_dir(self.db_path.parent)
        self.vault = vault or KeyVault(self.db_path.parent / ".vault.key")
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_keys (
                    provider_id TEXT PRIMARY KEY,
                    encrypted_key TEXT NOT NULL DEFAULT '',
                    base_url TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    models_json TEXT NOT NULL DEFAULT '[]',
                    last_tested_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'untested',
                    status_message TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    provider_id TEXT NOT NULL DEFAULT '',
                    model_id TEXT NOT NULL DEFAULT '',
                    route_reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conv
                    ON messages(conversation_id, created_at);

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            for k, v in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO app_settings(key, value) VALUES(?, ?)",
                    (k, v),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Settings & PIN
    # ------------------------------------------------------------------

    def get_settings_raw(self) -> dict[str, str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
            out = dict(DEFAULT_SETTINGS)
            for row in rows:
                out[row["key"]] = row["value"]
            return out

    def get_settings_public(self) -> dict[str, Any]:
        raw = self.get_settings_raw()
        pin_hash = raw.pop("access_pin_hash", "")
        return {
            **raw,
            "pin_enabled": bool(pin_hash),
        }

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            for key, value in updates.items():
                if key == "access_pin":
                    pin_str = "" if value is None else str(value).strip()
                    hashed = self.vault.hash_pin(pin_str) if pin_str else ""
                    conn.execute(
                        "INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)",
                        ("access_pin_hash", hashed),
                    )
                elif key in DEFAULT_SETTINGS and key != "access_pin_hash":
                    val_str = (
                        str(value).lower()
                        if isinstance(value, bool)
                        else ("" if value is None else str(value))
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)",
                        (key, val_str),
                    )
            conn.commit()
        return self.get_settings_public()

    def is_pin_enabled(self) -> bool:
        return bool(self.get_settings_raw().get("access_pin_hash", ""))

    def verify_pin(self, pin: str) -> bool:
        expected = self.get_settings_raw().get("access_pin_hash", "")
        return self.vault.verify_pin(pin, expected)

    # ------------------------------------------------------------------
    # Providers & Encrypted Keys
    # ------------------------------------------------------------------

    def save_provider(
        self,
        provider_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        enabled: bool | None = None,
        models: list[ModelInfo] | None = None,
        status: str | None = None,
        status_message: str | None = None,
        tested: bool = False,
    ) -> dict[str, Any]:
        if provider_id not in PROVIDER_MAP:
            raise ValueError(f"Unknown provider: {provider_id}")
        spec = PROVIDER_MAP[provider_id]

        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM provider_keys WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()

            enc_key = existing["encrypted_key"] if existing else ""
            if api_key is not None:
                clean_key = api_key.strip()
                enc_key = self.vault.encrypt(clean_key) if clean_key else ""

            final_base_url = (
                base_url.strip()
                if base_url is not None
                else (existing["base_url"] if existing else spec.default_base_url)
            )
            if not final_base_url:
                final_base_url = spec.default_base_url

            final_enabled = (
                int(enabled)
                if enabled is not None
                else (existing["enabled"] if existing else 1)
            )
            final_models_json = (
                json.dumps([m.to_dict() for m in models], ensure_ascii=False)
                if models is not None
                else (
                    existing["models_json"]
                    if existing
                    else json.dumps([m.to_dict() for m in CURATED_MODELS.get(provider_id, [])])
                )
            )
            final_status = (
                status
                if status is not None
                else (existing["status"] if existing else "untested")
            )
            final_msg = (
                status_message
                if status_message is not None
                else (existing["status_message"] if existing else "")
            )
            final_tested = _utc_now() if tested else (existing["last_tested_at"] if existing else "")

            conn.execute(
                """
                INSERT INTO provider_keys(
                    provider_id, encrypted_key, base_url, enabled,
                    models_json, last_tested_at, status, status_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    encrypted_key = excluded.encrypted_key,
                    base_url = excluded.base_url,
                    enabled = excluded.enabled,
                    models_json = excluded.models_json,
                    last_tested_at = excluded.last_tested_at,
                    status = excluded.status,
                    status_message = excluded.status_message
                """,
                (
                    provider_id,
                    enc_key,
                    final_base_url,
                    final_enabled,
                    final_models_json,
                    final_tested,
                    final_status,
                    final_msg,
                ),
            )
            conn.commit()
        return self.get_provider_public(provider_id)

    def delete_provider(self, provider_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM provider_keys WHERE provider_id = ?", (provider_id,))
            conn.commit()

    def get_provider_secret(self, provider_id: str) -> dict[str, Any] | None:
        spec = PROVIDER_MAP.get(provider_id)
        if not spec:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM provider_keys WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
        if not row:
            return None
        api_key = self.vault.decrypt(row["encrypted_key"])
        try:
            raw_models = json.loads(row["models_json"] or "[]")
            models = [ModelInfo.from_dict(m) for m in raw_models]
        except (json.JSONDecodeError, KeyError, TypeError):
            models = []
        return {
            "provider_id": provider_id,
            "api_key": api_key,
            "base_url": row["base_url"] or spec.default_base_url,
            "enabled": bool(row["enabled"]),
            "models": models,
            "status": row["status"],
            "status_message": row["status_message"],
            "last_tested_at": row["last_tested_at"],
        }

    def get_provider_public(self, provider_id: str) -> dict[str, Any]:
        spec = PROVIDER_MAP[provider_id]
        secret = self.get_provider_secret(provider_id)
        if not secret:
            default_models = [m.to_dict() for m in CURATED_MODELS.get(provider_id, [])]
            return {
                "id": spec.id,
                "name": spec.name,
                "adapter": spec.adapter,
                "requires_key": spec.requires_key,
                "signup_url": spec.signup_url,
                "color": spec.color,
                "badge": spec.badge,
                "free_note": spec.free_note,
                "free_note_bn": spec.free_note_bn,
                "base_url": spec.default_base_url,
                "has_key": False,
                "masked_key": "",
                "connected": False,
                "enabled": True,
                "status": "untested",
                "status_message": "",
                "last_tested_at": "",
                "models": default_models,
            }
        api_key = secret["api_key"]
        has_key = bool(api_key)
        connected = (
            (has_key and secret["status"] != "error")
            if spec.requires_key
            else (secret["status"] == "ok")
        )
        return {
            "id": spec.id,
            "name": spec.name,
            "adapter": spec.adapter,
            "requires_key": spec.requires_key,
            "signup_url": spec.signup_url,
            "color": spec.color,
            "badge": spec.badge,
            "free_note": spec.free_note,
            "free_note_bn": spec.free_note_bn,
            "base_url": secret["base_url"],
            "has_key": has_key,
            "masked_key": KeyVault.mask_key(api_key),
            "connected": connected,
            "enabled": secret["enabled"],
            "status": secret["status"],
            "status_message": secret["status_message"],
            "last_tested_at": secret["last_tested_at"],
            "models": [m.to_dict() for m in secret["models"]],
        }

    def list_providers_public(self) -> list[dict[str, Any]]:
        return [self.get_provider_public(spec.id) for spec in PROVIDER_SPECS]

    def get_connected_models(self) -> list[tuple[ModelInfo, dict[str, Any]]]:
        """Return all enabled models from connected providers along with provider secrets."""
        results: list[tuple[ModelInfo, dict[str, Any]]] = []
        for spec in PROVIDER_SPECS:
            secret = self.get_provider_secret(spec.id)
            if not secret or not secret["enabled"]:
                continue
            if spec.requires_key and not secret["api_key"]:
                continue
            if not spec.requires_key and secret["status"] != "ok":
                continue
            if secret["status"] == "auth_error":
                continue
            models = secret["models"] or CURATED_MODELS.get(spec.id, [])
            for model in models:
                if not model.free_verified:
                    continue
                results.append((model, secret))
        return results

    # ------------------------------------------------------------------
    # Conversations & Messages
    # ------------------------------------------------------------------

    def create_conversation(self, title: str = "New Chat") -> dict[str, Any]:
        conv_id = uuid.uuid4().hex[:12]
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations(id, title, pinned, created_at, updated_at)
                VALUES (?, ?, 0, ?, ?)
                """,
                (conv_id, title.strip() or "New Chat", now, now),
            )
            conn.commit()
        return self.get_conversation(conv_id)  # type: ignore[return-value]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "pinned": bool(row["pinned"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_conversations(self, search: str = "", limit: int = 80) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if search.strip():
                pattern = f"%{search.strip()}%"
                rows = conn.execute(
                    """
                    SELECT DISTINCT c.*
                    FROM conversations c
                    LEFT JOIN messages m ON m.conversation_id = c.id
                    WHERE c.title LIKE ? OR m.content LIKE ?
                    ORDER BY c.pinned DESC, c.updated_at DESC
                    LIMIT ?
                    """,
                    (pattern, pattern, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM conversations
                    ORDER BY pinned DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "pinned": bool(r["pinned"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def update_conversation(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if not row:
                return None
            new_title = title.strip()[:120] if title is not None else row["title"]
            if not new_title:
                new_title = row["title"]
            new_pinned = int(pinned) if pinned is not None else row["pinned"]
            conn.execute(
                """
                UPDATE conversations
                SET title = ?, pinned = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_title, new_pinned, _utc_now(), conversation_id),
            )
            conn.commit()
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            conn.commit()
            return cur.rowcount > 0

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        provider_id: str = "",
        model_id: str = "",
        route_reason: str = "",
    ) -> dict[str, Any]:
        msg_id = uuid.uuid4().hex[:16]
        now = _utc_now()
        # Store lightweight attachment metadata (avoid bloating SQLite with duplicate huge blobs if unneeded,
        # though keeping small preview thumbnails/names works nicely; strip raw >150KB data from history storage)
        safe_att: list[dict[str, Any]] = []
        for att in attachments or []:
            item = {
                "name": att.get("name", "attachment"),
                "mime_type": att.get("mime_type", "application/octet-stream"),
            }
            data = att.get("data", "")
            if item["mime_type"].startswith("image/") and isinstance(data, str) and len(data) <= 220_000:
                item["data"] = data
            safe_att.append(item)

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages(
                    id, conversation_id, role, content, attachments_json,
                    provider_id, model_id, route_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg_id,
                    conversation_id,
                    role,
                    content,
                    json.dumps(safe_att, ensure_ascii=False),
                    provider_id,
                    model_id,
                    route_reason,
                    now,
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            conn.commit()
        return {
            "id": msg_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "attachments": safe_att,
            "provider_id": provider_id,
            "model_id": model_id,
            "route_reason": route_reason,
            "created_at": now,
        }

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (conversation_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                atts = json.loads(r["attachments_json"] or "[]")
            except json.JSONDecodeError:
                atts = []
            out.append(
                {
                    "id": r["id"],
                    "conversation_id": r["conversation_id"],
                    "role": r["role"],
                    "content": r["content"],
                    "attachments": atts,
                    "provider_id": r["provider_id"],
                    "model_id": r["model_id"],
                    "route_reason": r["route_reason"],
                    "created_at": r["created_at"],
                }
            )
        return out

    def delete_last_assistant_message(self, conversation_id: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, role FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if not row or row["role"] != "assistant":
                return False
            conn.execute("DELETE FROM messages WHERE id = ?", (row["id"],))
            conn.commit()
            return True


db = Database()
