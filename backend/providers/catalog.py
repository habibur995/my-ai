"""Curated registry of 100% free-tier AI providers (no credit card required)."""

from __future__ import annotations

from dataclasses import dataclass

from backend.providers.base import ModelInfo


@dataclass(frozen=True)
class ProviderSpec:
    """Static definition of a supported free-tier AI provider."""

    id: str
    name: str
    adapter: str  # "gemini" | "openai_compat"
    requires_key: bool
    default_base_url: str
    signup_url: str
    color: str
    badge: str
    free_note: str
    free_note_bn: str


PROVIDER_SPECS: list[ProviderSpec] = [
    ProviderSpec(
        id="gemini",
        name="Google AI Studio (Gemini)",
        adapter="gemini",
        requires_key=True,
        default_base_url="",
        signup_url="https://aistudio.google.com/apikey",
        color="#4f8cff",
        badge="Google",
        free_note="Free tier · No card required · Gemini Flash-Lite, Gemma 4 & Live Voice",
        free_note_bn="কার্ড ছাড়াই ফ্রি · Gemini Flash-Lite, Gemma 4 ও লাইভ ভয়েস",
    ),
    ProviderSpec(
        id="groq",
        name="Groq Cloud",
        adapter="openai_compat",
        requires_key=True,
        default_base_url="https://api.groq.com/openai/v1",
        signup_url="https://console.groq.com/keys",
        color="#f97316",
        badge="LPU Fast",
        free_note="Free tier · No card required · Ultra-fast Llama/Qwen + Whisper Voice STT",
        free_note_bn="কার্ড ছাড়াই ফ্রি · সুপার-ফাস্ট Llama/Qwen + বাংলা ভয়েস STT",
    ),
    ProviderSpec(
        id="openrouter",
        name="OpenRouter (Free Only)",
        adapter="openai_compat",
        requires_key=True,
        default_base_url="https://openrouter.ai/api/v1",
        signup_url="https://openrouter.ai/keys",
        color="#14b8a6",
        badge=":free Only",
        free_note="Strictly filtered to :free models · Paid models blocked automatically",
        free_note_bn="শুধু :free মডেল চলবে · পেইড মডেল অটোমেটিক ব্লক করা",
    ),
    ProviderSpec(
        id="nvidia",
        name="NVIDIA NIM",
        adapter="openai_compat",
        requires_key=True,
        default_base_url="https://integrate.api.nvidia.com/v1",
        signup_url="https://build.nvidia.com",
        color="#22c55e",
        badge="NVIDIA",
        free_note="Free developer prototyping tier · No card required",
        free_note_bn="কার্ড ছাড়াই ফ্রি ডেভেলপার টিয়ার · Nemotron, Llama ও Qwen",
    ),
    ProviderSpec(
        id="cohere",
        name="Cohere (Trial Key)",
        adapter="openai_compat",
        requires_key=True,
        default_base_url="https://api.cohere.ai/compatibility/v1",
        signup_url="https://dashboard.cohere.com/api-keys",
        color="#f43f5e",
        badge="Cohere",
        free_note="Free trial key (~1,000 calls/month) · No card required",
        free_note_bn="কার্ড ছাড়াই ফ্রি ট্রায়াল (মাসে ~১,০০০ কল) · বহুভাষিক Command মডেল",
    ),
    ProviderSpec(
        id="huggingface",
        name="Hugging Face Inference",
        adapter="openai_compat",
        requires_key=True,
        default_base_url="https://router.huggingface.co/v1",
        signup_url="https://huggingface.co/settings/tokens",
        color="#eab308",
        badge="HF Hub",
        free_note="Free serverless router · No card required",
        free_note_bn="কার্ড ছাড়াই ফ্রি সার্ভারলেস মডেল (Qwen, Llama)",
    ),
    ProviderSpec(
        id="ollama",
        name="Ollama / LM Studio (Local)",
        adapter="openai_compat",
        requires_key=False,
        default_base_url="http://127.0.0.1:11434/v1",
        signup_url="https://ollama.com",
        color="#94a3b8",
        badge="Offline",
        free_note="100% Free & Offline on your Windows PC · No API key needed",
        free_note_bn="১০০% ফ্রি ও অফলাইন · নিজের পিসিতে চলে, কোনো API key লাগে না",
    ),
]

PROVIDER_MAP: dict[str, ProviderSpec] = {spec.id: spec for spec in PROVIDER_SPECS}

# Curated fallback/metadata list using ONLY current non-deprecated models.
CURATED_MODELS: dict[str, list[ModelInfo]] = {
    "gemini": [
        ModelInfo(
            id="gemini-3.5-flash-lite",
            label="Gemini 3.5 Flash-Lite",
            provider_id="gemini",
            tags=["Fast", "Vision", "1M", "Multilingual"],
            context_k=1000,
            vision=True,
            coding_rank=84,
            speed_rank=96,
        ),
        ModelInfo(
            id="gemini-3.1-flash-lite",
            label="Gemini 3.1 Flash-Lite",
            provider_id="gemini",
            tags=["Ultra-Fast", "Vision", "1M"],
            context_k=1000,
            vision=True,
            coding_rank=78,
            speed_rank=98,
        ),
        ModelInfo(
            id="gemini-4-hydrogen",
            label="Gemini 4 Hydrogen",
            provider_id="gemini",
            tags=["Balanced", "Code", "Vision", "1M"],
            context_k=1000,
            vision=True,
            coding_rank=94,
            speed_rank=88,
        ),
        ModelInfo(
            id="gemma-4-31b-it",
            label="Gemma 4 31B",
            provider_id="gemini",
            tags=["Open", "Reasoning", "Code"],
            context_k=128,
            vision=False,
            coding_rank=86,
            speed_rank=80,
        ),
        ModelInfo(
            id="gemma-4-26b-a4b-it",
            label="Gemma 4 26B A4B (MoE)",
            provider_id="gemini",
            tags=["Open", "MoE", "Vision"],
            context_k=128,
            vision=True,
            coding_rank=84,
            speed_rank=87,
        ),
    ],
    "groq": [
        ModelInfo(
            id="llama-3.3-70b-versatile",
            label="Llama 3.3 70B Versatile",
            provider_id="groq",
            tags=["LPU Fast", "Reasoning", "Multilingual"],
            context_k=128,
            vision=False,
            coding_rank=86,
            speed_rank=95,
        ),
        ModelInfo(
            id="qwen/qwen3-32b",
            label="Qwen 3 32B",
            provider_id="groq",
            tags=["Code", "Reasoning", "Fast"],
            context_k=128,
            vision=False,
            coding_rank=88,
            speed_rank=94,
        ),
        ModelInfo(
            id="openai/gpt-oss-120b",
            label="GPT-OSS 120B",
            provider_id="groq",
            tags=["Large", "Reasoning", "Code"],
            context_k=128,
            vision=False,
            coding_rank=90,
            speed_rank=88,
        ),
        ModelInfo(
            id="meta-llama/llama-4-scout-17b-16e-instruct",
            label="Llama 4 Scout 17B (Vision)",
            provider_id="groq",
            tags=["Vision", "Fast"],
            context_k=128,
            vision=True,
            coding_rank=79,
            speed_rank=95,
        ),
        ModelInfo(
            id="llama-3.1-8b-instant",
            label="Llama 3.1 8B Instant",
            provider_id="groq",
            tags=["Instant", "High Quota"],
            context_k=128,
            vision=False,
            coding_rank=68,
            speed_rank=99,
        ),
    ],
    "openrouter": [
        ModelInfo(
            id="openrouter/free",
            label="OpenRouter Auto (:free)",
            provider_id="openrouter",
            tags=["Auto :free", "Zero Cost"],
            context_k=128,
            vision=False,
            coding_rank=80,
            speed_rank=82,
        ),
    ],
    "nvidia": [
        ModelInfo(
            id="qwen/qwen2.5-coder-32b-instruct",
            label="Qwen 2.5 Coder 32B",
            provider_id="nvidia",
            tags=["Code", "Instruct"],
            context_k=32,
            vision=False,
            coding_rank=89,
            speed_rank=78,
        ),
        ModelInfo(
            id="meta/llama-3.3-70b-instruct",
            label="Llama 3.3 70B Instruct",
            provider_id="nvidia",
            tags=["Reasoning", "General"],
            context_k=128,
            vision=False,
            coding_rank=85,
            speed_rank=76,
        ),
        ModelInfo(
            id="nvidia/llama-3.1-nemotron-70b-instruct",
            label="Nemotron 70B Instruct",
            provider_id="nvidia",
            tags=["Reasoning", "NVIDIA"],
            context_k=128,
            vision=False,
            coding_rank=84,
            speed_rank=74,
        ),
    ],
    "cohere": [
        ModelInfo(
            id="command-a-03-2025",
            label="Command A",
            provider_id="cohere",
            tags=["Multilingual", "Reasoning"],
            context_k=256,
            vision=False,
            coding_rank=84,
            speed_rank=76,
        ),
        ModelInfo(
            id="command-r7b-12-2024",
            label="Command R7B",
            provider_id="cohere",
            tags=["Fast", "Multilingual"],
            context_k=128,
            vision=False,
            coding_rank=72,
            speed_rank=88,
        ),
    ],
    "huggingface": [
        ModelInfo(
            id="Qwen/Qwen2.5-72B-Instruct",
            label="Qwen 2.5 72B Instruct",
            provider_id="huggingface",
            tags=["Open", "Code", "Multilingual"],
            context_k=128,
            vision=False,
            coding_rank=87,
            speed_rank=72,
        ),
        ModelInfo(
            id="meta-llama/Llama-3.3-70B-Instruct",
            label="Llama 3.3 70B Instruct",
            provider_id="huggingface",
            tags=["Open", "General"],
            context_k=128,
            vision=False,
            coding_rank=84,
            speed_rank=74,
        ),
    ],
    "ollama": [],
}
