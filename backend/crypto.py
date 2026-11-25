"""Symmetric Fernet vault for encrypting API keys and hashing optional PINs."""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from backend.config import KEY_PATH


class KeyVault:
    """Encrypts/decrypts provider API keys using a locally persisted Fernet key."""

    def __init__(self, key_path: Path = KEY_PATH) -> None:
        self.key_path = Path(key_path)
        self._key = self._load_or_create_key()
        self._fernet = Fernet(self._key)

    def _load_or_create_key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            raw = self.key_path.read_text(encoding="utf-8").strip().encode("utf-8")
            if raw:
                return raw
        key = Fernet.generate_key()
        self.key_path.write_text(key.decode("utf-8"), encoding="utf-8")
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a secret string; returns empty string for empty input."""
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a secret string; returns empty string on empty or invalid input."""
        if not ciphertext:
            return ""
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""

    def hash_pin(self, pin: str) -> str:
        """Create a keyed SHA-256 digest of a mobile access PIN."""
        clean = pin.strip()
        if not clean:
            return ""
        return hmac.new(self._key, clean.encode("utf-8"), hashlib.sha256).hexdigest()

    def verify_pin(self, pin: str, expected_hash: str) -> bool:
        """Constant-time verification of a user-entered PIN against stored hash."""
        if not expected_hash:
            return True
        candidate = self.hash_pin(pin)
        return bool(candidate) and hmac.compare_digest(candidate, expected_hash)

    @staticmethod
    def mask_key(plaintext: str) -> str:
        """Return a safe masked preview of an API key for UI display."""
        if not plaintext:
            return ""
        if len(plaintext) <= 8:
            return "•" * len(plaintext)
        return f"{plaintext[:4]}••••{plaintext[-4:]}"
