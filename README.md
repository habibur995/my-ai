# FreeAI Hub — ১০০% ফ্রি মাল্টি-মডেল AI ও লাইভ ভয়েস হাব

উইন্ডোজ পিসি এবং যেকোনো মোবাইল ফোন থেকে চালানোর উপযোগী একটি **100% Free-API-Only** মাল্টি-মডেল AI ওয়েব অ্যাপ (PWA)। কোনো পেইড সাবস্ক্রিপশন বা ক্রেডিট কার্ড ছাড়াই ফ্রি API key যোগ করে ব্যবহার করা যায়।

## মূল ফিচারসমূহ
- **১০০% ফ্রি API হাব (No Card):**
  - **Google AI Studio (`Gemini`)**: [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — Gemini Flash-Lite, Gemini 4 Hydrogen, Gemma 4 ও Live Voice।
  - **Groq Cloud**: [console.groq.com/keys](https://console.groq.com/keys) — সুপার-ফাস্ট Llama / Qwen / GPT-OSS এবং ফ্রি Whisper Large v3 Turbo।
  - **OpenRouter (`:free` Only)**: [openrouter.ai/keys](https://openrouter.ai/keys) — ব্যাকএন্ড গার্ড দিয়ে শুধু `:free` মডেল চালু থাকে।
  - **NVIDIA NIM**, **Cohere Trial**, **Hugging Face**, ও অফলাইন **Ollama / LM Studio**।
- **Smart Auto-Router ও 429 Auto-Fallback:**
  - প্রম্পটের ধরন (Coding, Vision/ছবি, Long Context, বাংলা/Multilingual) বুঝে অটোমেটিক সেরা ফ্রি মডেল বেছে নেয়।
  - কোনো ফ্রি API-তে রেট লিমিট (`429`) এলে স্বয়ংক্রিয়ভাবে পরের ফ্রি মডেলে সুইচ করে।
- **যেকোনো ভাষায় Live Streaming Voice:**
  - **Gemini 3.8 Live** (নেটিভ অডিও WebSocket) + **Universal Free Voice** (Groq Whisper / Gemini STT + Auto-Routed LLM + Edge-TTS বাংলা `bn-BD-NabanitaNeural` ও ৭০+ ভাষায়)।
- **Compare Mode, Markdown + Code Copy, ছবি/ফাইল আপলোড, Dark/Light থিম ও ঐচ্ছিক PIN লক।**

## কীভাবে চালাবেন (Windows + Mobile)

### ১. Windows পিসিতে চালু করুন
1. `C:\Users\oviha\Desktop\free-ai-hub` ফোল্ডারে থাকা [`start.bat`](./start.bat) ডাবল-ক্লিক করুন।
2. ব্রাউজার অটোমেটিক `http://localhost:8000` ওপেন করবে।
3. **Settings (⚙️) → Free API Keys** থেকে আপনার ফ্রি API key পেস্ট করে **Test & Save** করুন।

### ২. মোবাইল ফোন থেকে ব্যবহার করুন
- **একই Wi-Fi-তে (চ্যাট):** `start.bat` উইন্ডোতে দেখানো `http://<আপনার-পিসির-IP>:8000` ঠিকানা ফোনের ব্রাউজারে ওপেন করুন এবং **Add to Home Screen** করে PWA অ্যাপ হিসেবে ইন্সটল করুন।
- **যেকোনো জায়গা থেকে + মোবাইলে Live Voice Mic (HTTPS):**
  1. আগে **Settings → পছন্দ ও সিকিউরিটি** থেকে একটি PIN সেট করে নিন।
  2. এরপর [`start_mobile_tunnel.bat`](./start_mobile_tunnel.bat) চালান — এটি একটি ফ্রি `https://*.trycloudflare.com` লিংক দেবে, যা যেকোনো মোবাইল ব্রাউজারে মাইক্রোফোনসহ পুরোপুরি কাজ করবে।

## প্রজেক্ট স্ট্রাকচার
- `app.py` — FastAPI সার্ভার (HTTP + SSE + WebSocket + PWA)
- `backend/` — এনক্রিপ্টেড Key Vault (`crypto.py`), SQLite স্টোরেজ (`db.py`), ফ্রি প্রোভাইডার অ্যাডাপ্টার (`providers/`), Auto-Router (`router.py`), এবং Dual Voice Engine (`voice.py`)
- `frontend/` — রেসপনসিভ Dark/Light PWA ইন্টারফেস ও AudioWorklet ভয়েস ক্লায়েন্ট
- `tests/test_app.py` — স্বয়ংক্রিয় pytest টেস্ট স্যুইট

## ফ্রি ক্লাউড হোস্টিং (Render.com — 1-Click Docker)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/habibur995/my-ai)

1. উপরের **Deploy to Render** বাটনে ক্লিক করে GitHub দিয়ে সাইন ইন করুন (`render.yaml` অটো-ডিটেক্ট হবে)।
2. ফ্রি টিয়ারে কন্টেইনার রিস্টার্ট হলে API key স্থায়ী রাখতে Render Dashboard → **Environment** ট্যাবে নিচের যেকোনো এনভায়রনমেন্ট ভ্যারিয়েবল যোগ করুন:
   - `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `COHERE_API_KEY`, `HF_API_KEY`
   - `FREEAI_ACCESS_PIN` (মোবাইল/পাবলিক অ্যাক্সেস লক করতে)
