"""Configuration, paths, defaults, and network helpers for FreeAI Hub."""

from __future__ import annotations

import os
import socket
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
_env_data_dir = os.environ.get("FREEAI_DATA_DIR", "").strip()
DATA_DIR = Path(_env_data_dir) if _env_data_dir else (BASE_DIR / "data")
DB_PATH = DATA_DIR / "free_ai_hub.db"
KEY_PATH = DATA_DIR / ".vault.key"
FRONTEND_DIR = BASE_DIR / "frontend"

DEFAULT_PORT = int(os.environ.get("PORT", "8000"))

DEFAULT_SYSTEM_PROMPT = (
    "You are FreeAI Hub, a helpful, accurate, and concise AI assistant. "
    "Always reply in the same language the user uses (for example Bengali or English), "
    "format code cleanly in Markdown blocks when helpful, and be honest if you are unsure."
)

DEFAULT_SETTINGS: dict[str, str] = {
    "theme": "dark",
    "default_mode": "auto",
    "auto_fallback": "true",
    "memory_enabled": "true",
    "voice_engine": "auto",
    "voice_language": "auto",
    "voice_gender": "female",
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "access_pin_hash": "",
}


def ensure_data_dir(data_dir: Path = DATA_DIR) -> Path:
    """Ensure the runtime data directory exists."""
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_lan_ips() -> list[str]:
    """Discover non-loopback local IPv4 addresses for mobile LAN access."""
    ips: list[str] = []
    # Primary route probe without sending any packet
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.2)
            sock.connect(("8.8.8.8", 80))
            primary = sock.getsockname()[0]
            if primary and not primary.startswith("127.") and not primary.startswith("169.254."):
                ips.append(primary)
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if (
                ip
                and not ip.startswith("127.")
                and not ip.startswith("169.254.")
                and ip not in ips
            ):
                ips.append(ip)
    except OSError:
        pass

    return ips
