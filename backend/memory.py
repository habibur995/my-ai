"""Background long-term user memory extraction and prompt formatting."""

from __future__ import annotations

import re
from typing import Any

# Explicit remember commands (Bengali + English)
_EXPLICIT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?:মনে\s*রাখ(?:বে|ো|বেন|ুন)?|মনে\s*রেখো|ভুলো\s*না(?:\s*যে)?|মেমোরিতে\s*রাখ(?:ো|ুন)?)"
            r"[\s:,-]*(?:যে\s+)?(.{4,240})",
            re.IGNORECASE,
        ),
        "explicit",
    ),
    (
        re.compile(
            r"(?:remember\s+that|remember\s*:|don'?t\s+forget\s+that|save\s+to\s+memory\s*:?)"
            r"\s*(.{4,240})",
            re.IGNORECASE,
        ),
        "explicit",
    ),
]

# Implicit durable user facts (name, profession, location, language/style preference)
_PROFILE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(আমার\s+নাম\s+[^\n।.!?]{2,60})",
            re.IGNORECASE,
        ),
        "profile",
    ),
    (
        re.compile(
            r"(?:^|[.!?।\n]\s*)(my\s+name\s+is\s+[^\n.!?]{2,60}|call\s+me\s+[^\n.!?]{2,50})",
            re.IGNORECASE,
        ),
        "profile",
    ),
    (
        re.compile(
            r"(আমি\s+(?:একজন\s+)?[^\n।.!?]{3,80}?(?:কাজ\s*করি|পড়ি|পড়াশোনা\s*করি|ডেভেলপার|ইঞ্জিনিয়ার|ছাত্র|শিক্ষার্থী|থাকি))",
            re.IGNORECASE,
        ),
        "profile",
    ),
    (
        re.compile(
            r"(?:^|[.!?।\n]\s*)(i\s+(?:am\s+a|work\s+as|live\s+in|study)\s+[^\n.!?]{3,90})",
            re.IGNORECASE,
        ),
        "profile",
    ),
    (
        re.compile(
            r"(সবসময়\s+[^\n।.!?]{4,120}(?:উত্তর|কথা|লিখ|বল)[^\n।.!?]{0,40})",
            re.IGNORECASE,
        ),
        "preference",
    ),
    (
        re.compile(
            r"(?:^|[.!?।\n]\s*)(always\s+(?:reply|respond|answer|speak|write)\s+[^\n.!?]{3,120})",
            re.IGNORECASE,
        ),
        "preference",
    ),
]


def _clean_memory_snippet(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = cleaned.strip(" \"'.,।;:-")
    return cleaned[:240]


def extract_memories_fast(user_text: str) -> list[tuple[str, str]]:
    """Deterministically extract explicit or high-confidence personal memories (0ms)."""
    text = (user_text or "").strip()
    if len(text) < 5:
        return []

    found: list[tuple[str, str]] = []
    seen_lower: set[str] = set()

    for pat, cat in _EXPLICIT_PATTERNS:
        m = pat.search(text)
        if m:
            snippet = _clean_memory_snippet(m.group(1))
            if len(snippet) >= 4 and snippet.lower() not in seen_lower:
                seen_lower.add(snippet.lower())
                found.append((snippet, cat))

    for pat, cat in _PROFILE_PATTERNS:
        m = pat.search(text)
        if m:
            snippet = _clean_memory_snippet(m.group(1))
            if len(snippet) >= 5 and snippet.lower() not in seen_lower:
                # Skip if already captured inside an explicit memory
                if any(snippet.lower() in ex for ex in seen_lower):
                    continue
                seen_lower.add(snippet.lower())
                found.append((snippet, cat))

    return found[:3]


def format_memory_prompt(memories: list[dict[str, Any]]) -> str:
    """Format persisted user memories into a concise system prompt block."""
    if not memories:
        return ""
    lines = [
        "Background User Memory (remember these facts and preferences across all chats; use naturally without awkwardly repeating them unless relevant):"
    ]
    for mem in memories[:40]:
        content = str(mem.get("content", "")).strip()
        if content:
            lines.append(f"- {content}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def build_system_instruction(
    base_prompt: str,
    memories: list[dict[str, Any]] | None = None,
    *,
    memory_enabled: bool = True,
) -> str:
    """Combine base system prompt with optional background memory context."""
    parts = [base_prompt.strip()] if (base_prompt or "").strip() else []
    if memory_enabled and memories:
        mem_block = format_memory_prompt(memories)
        if mem_block:
            parts.append(mem_block)
    return "\n\n".join(parts)
