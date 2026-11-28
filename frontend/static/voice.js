(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);

  const vEls = {
    overlay: $('voiceOverlay'),
    engineBadge: $('voiceEngineBadge'),
    modelBadge: $('voiceActiveModelBadge'),
    engineSelect: $('voiceEngineSelect'),
    langSelect: $('voiceLangSelect'),
    genderSelect: $('voiceGenderSelect'),
    closeBtn: $('closeVoiceBtn'),
    orb: $('voiceOrb'),
    stateText: $('voiceStateText'),
    subState: $('voiceSubState'),
    micNotice: $('voiceMicNotice'),
    transcriptBox: $('voiceTranscriptBox'),
    vadModeBtn: $('voiceVadModeBtn'),
    mainBtn: $('voiceMainBtn'),
    interruptBtn: $('voiceInterruptBtn'),
    endBtn: $('voiceEndBtn'),
    textForm: $('voiceTextForm'),
    textInput: $('voiceTextInput'),
  };

  const voiceState = {
    isOpen: false,
    ws: null,
    engine: 'auto',
    hasServerStt: true,
    vadMode: 'handsfree', // 'handsfree' | 'ptt'
    isRecording: false,
    isSpeaking: false,
    micStream: null,
    captureCtx: null,
    workletNode: null,
    playbackCtx: null,
    nextPlayTime: 0,
    activeSources: new Set(),
    decodeChain: Promise.resolve(),
    // Hands-free VAD tracking
    speechStarted: false,
    lastLoudAt: 0,
    silenceTimer: null,
    assistantLineEl: null,
    userLineEl: null,
    speechRec: null,
  };

  function ensurePlaybackContext() {
    if (!voiceState.playbackCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      voiceState.playbackCtx = new AC();
    }
    if (voiceState.playbackCtx.state === 'suspended') {
      voiceState.playbackCtx.resume().catch(() => {});
    }
    return voiceState.playbackCtx;
  }

  function stopPlayback() {
    for (const src of voiceState.activeSources) {
      try {
        src.stop();
      } catch {}
    }
    voiceState.activeSources.clear();
    voiceState.nextPlayTime = 0;
    voiceState.isSpeaking = false;
    vEls.interruptBtn.classList.add('hidden');
  }

  function setVisualState(mode, title, sub = '') {
    vEls.orb.className = `voice-orb ${mode}`;
    vEls.stateText.textContent = title;
    if (sub) vEls.subState.textContent = sub;
  }

  function appendTranscript(role, text, append = false) {
    if (!text) return;
    if (append && role === 'assistant' && voiceState.assistantLineEl) {
      voiceState.assistantLineEl.textContent += text;
      vEls.transcriptBox.scrollTop = vEls.transcriptBox.scrollHeight;
      return;
    }
    if (append && role === 'user' && voiceState.userLineEl) {
      voiceState.userLineEl.textContent += text;
      vEls.transcriptBox.scrollTop = vEls.transcriptBox.scrollHeight;
      return;
    }

    const line = document.createElement('div');
    line.className = `v-line ${role}`;
    line.textContent = `${role === 'user' ? 'আপনি: ' : 'AI: '}${text}`;
    vEls.transcriptBox.appendChild(line);
    vEls.transcriptBox.scrollTop = vEls.transcriptBox.scrollHeight;

    if (role === 'assistant') {
      voiceState.assistantLineEl = line;
      voiceState.userLineEl = null;
    } else {
      voiceState.userLineEl = line;
      voiceState.assistantLineEl = null;
    }
  }

  function b64ToArrayBuffer(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) {
      bytes[i] = bin.charCodeAt(i);
    }
    return bytes.buffer;
  }

  function scheduleBuffer(audioBuffer) {
    const ctx = ensurePlaybackContext();
    const src = ctx.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime + 0.02, voiceState.nextPlayTime);
    voiceState.nextPlayTime = startAt + audioBuffer.duration;
    voiceState.activeSources.add(src);
    voiceState.isSpeaking = true;
    vEls.interruptBtn.classList.remove('hidden');
    setVisualState('speaking', 'AI বলছে...', 'কথা থামাতে চাইলে বলুন বা "থামান" চাপুন');

    src.onended = () => {
      voiceState.activeSources.delete(src);
      if (voiceState.activeSources.size === 0) {
        voiceState.isSpeaking = false;
        vEls.interruptBtn.classList.add('hidden');
        if (voiceState.isRecording) {
          setVisualState('listening', 'শুনছি...', 'যেকোনো ভাষায় কথা বলুন');
        } else {
          setVisualState('idle', 'কথা বলতে ট্যাপ করুন');
        }
      }
    };
    src.start(startAt);
  }

  function playPcm24k(b64, sampleRate = 24000) {
    const ctx = ensurePlaybackContext();
    const raw = b64ToArrayBuffer(b64);
    const int16 = new Int16Array(raw);
    if (!int16.length) return;
    const audioBuf = ctx.createBuffer(1, int16.length, sampleRate);
    const ch = audioBuf.getChannelData(0);
    for (let i = 0; i < int16.length; i++) {
      ch[i] = int16[i] / 32768.0;
    }
    scheduleBuffer(audioBuf);
  }

  function playMp3(b64) {
    const ctx = ensurePlaybackContext();
    const raw = b64ToArrayBuffer(b64);
    voiceState.decodeChain = voiceState.decodeChain
      .then(() => ctx.decodeAudioData(raw))
      .then((decoded) => scheduleBuffer(decoded))
      .catch(() => {});
  }

  async function startMicrophone() {
    if (voiceState.isRecording) return;

    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      vEls.micNotice.innerHTML =
        'এই ব্রাউজার ঠিকানায় মাইক্রোফোন অনুমতি পাওয়া যায়নি (মোবাইলে HTTPS প্রয়োজন)। পিসির ফোল্ডারে <code>start_mobile_tunnel.bat</code> চালিয়ে HTTPS লিংক দিয়ে খুলুন, অথবা নিচে লিখে কথা বলুন।';
      vEls.micNotice.classList.remove('hidden');
      setVisualState('idle', 'মাইক পাওয়া যায়নি (HTTPS দরকার)', 'নিচের বক্সে লিখে কথা বলতে পারেন');
      return;
    }

    // If Universal Voice lacks Groq/Gemini STT key, fallback to browser SpeechRecognition if present
    if (voiceState.engine === 'universal' && !voiceState.hasServerStt) {
      return startBrowserSpeechFallback();
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      voiceState.micStream = stream;

      const AC = window.AudioContext || window.webkitAudioContext;
      const capCtx = new AC();
      voiceState.captureCtx = capCtx;
      if (capCtx.state === 'suspended') {
        await capCtx.resume();
      }
      await capCtx.audioWorklet.addModule('/static/pcm-processor.js');

      const source = capCtx.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(capCtx, 'pcm-capture-processor');
      voiceState.workletNode = worklet;
      const preRoll = [];

      worklet.port.onmessage = (event) => {
        if (!voiceState.isRecording || !voiceState.ws || voiceState.ws.readyState !== 1) return;
        const { pcm, rms } = event.data;

        // Visual orb reaction to mic level
        const scale = 1 + Math.min(0.24, rms * 2.2);
        if (vEls.orb.classList.contains('listening')) {
          vEls.orb.style.transform = `scale(${scale.toFixed(3)})`;
        }

        if (voiceState.engine === 'gemini_live') {
          if (voiceState.isSpeaking && rms > 0.045) {
            stopPlayback();
          }
          voiceState.ws.send(pcm);
          return;
        }

        if (voiceState.vadMode === 'ptt') {
          voiceState.ws.send(pcm);
          return;
        }

        // Universal Hands-Free VAD
        if (voiceState.isSpeaking) {
          if (rms > 0.045) {
            stopPlayback();
            voiceState.ws.send(JSON.stringify({ type: 'interrupt' }));
            voiceState.speechStarted = true;
            voiceState.lastLoudAt = performance.now();
            while (preRoll.length) voiceState.ws.send(preRoll.shift());
            voiceState.ws.send(pcm);
          }
          return;
        }

        if (rms > 0.02) {
          voiceState.lastLoudAt = performance.now();
          if (!voiceState.speechStarted) {
            voiceState.speechStarted = true;
            while (preRoll.length) voiceState.ws.send(preRoll.shift());
          }
        }

        if (voiceState.speechStarted) {
          voiceState.ws.send(pcm);
          if (rms < 0.015 && performance.now() - voiceState.lastLoudAt > 1150) {
            voiceState.speechStarted = false;
            preRoll.length = 0;
            voiceState.ws.send(JSON.stringify({ type: 'audio_end' }));
            setVisualState('thinking', 'বুঝছি ও উত্তর তৈরি করছি...');
          }
        } else {
          preRoll.push(pcm);
          if (preRoll.length > 6) preRoll.shift();
        }
      };

      source.connect(worklet);
      // Connect through silent gain so browsers keep processing AudioWorklet
      const silentGain = capCtx.createGain();
      silentGain.gain.value = 0;
      worklet.connect(silentGain);
      silentGain.connect(capCtx.destination);

      voiceState.isRecording = true;
      voiceState.speechStarted = false;
      vEls.mainBtn.classList.add('active');
      vEls.mainBtn.textContent = voiceState.vadMode === 'ptt' ? '📤 পাঠানো শেষ করুন' : '⏸ মাইক মিউট করুন';
      setVisualState('listening', 'শুনছি...', 'যেকোনো ভাষায় কথা বলুন');
      vEls.micNotice.classList.add('hidden');
    } catch (err) {
      vEls.micNotice.textContent = `মাইক্রোফোন চালু করা যায়নি: ${err.message}`;
      vEls.micNotice.classList.remove('hidden');
    }
  }

  function startBrowserSpeechFallback() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      vEls.micNotice.textContent =
        'Universal ভয়েস STT-এর জন্য Groq বা Gemini API key যোগ করুন অথবা নিচে লিখে কথা বলুন।';
      vEls.micNotice.classList.remove('hidden');
      return;
    }
    const rec = new SR();
    const langVal = vEls.langSelect.value;
    rec.lang = langVal === 'auto' ? 'bn-BD' : langVal;
    rec.interimResults = false;
    rec.continuous = true;
    rec.onresult = (ev) => {
      const res = ev.results[ev.results.length - 1];
      if (res && res.isFinal && voiceState.ws?.readyState === 1) {
        const text = res[0].transcript.trim();
        if (text) {
          stopPlayback();
          voiceState.ws.send(JSON.stringify({ type: 'text', text }));
          setVisualState('thinking', 'উত্তর তৈরি হচ্ছে...');
        }
      }
    };
    rec.onerror = () => {};
    rec.start();
    voiceState.speechRec = rec;
    voiceState.isRecording = true;
    vEls.mainBtn.classList.add('active');
    vEls.mainBtn.textContent = '⏸ মাইক বন্ধ';
    setVisualState('listening', 'শুনছি (Browser Speech)...');
  }

  function stopMicrophone(sendEnd = true) {
    if (voiceState.speechRec) {
      try {
        voiceState.speechRec.stop();
      } catch {}
      voiceState.speechRec = null;
    }
    if (voiceState.workletNode) {
      try {
        voiceState.workletNode.disconnect();
      } catch {}
      voiceState.workletNode = null;
    }
    if (voiceState.captureCtx) {
      voiceState.captureCtx.close().catch(() => {});
      voiceState.captureCtx = null;
    }
    if (voiceState.micStream) {
      voiceState.micStream.getTracks().forEach((t) => t.stop());
      voiceState.micStream = null;
    }
    if (sendEnd && voiceState.isRecording && voiceState.ws?.readyState === 1) {
      voiceState.ws.send(JSON.stringify({ type: 'audio_end' }));
    }
    voiceState.isRecording = false;
    voiceState.speechStarted = false;
    vEls.orb.style.transform = '';
    vEls.mainBtn.classList.remove('active');
    vEls.mainBtn.textContent = '🎙️ কথা বলুন';
  }

  function connectSocket() {
    const ctx = window.FreeAIHub?.getContext() || {};
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const pinQuery = ctx.pin ? `?pin=${encodeURIComponent(ctx.pin)}` : '';
    const ws = new WebSocket(`${proto}//${location.host}/ws/voice${pinQuery}`);
    voiceState.ws = ws;

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          engine: vEls.engineSelect.value,
          language: vEls.langSelect.value,
          gender: vEls.genderSelect.value,
          mode: ctx.mode || 'auto',
          provider_id: ctx.provider_id || '',
          model_id: ctx.model_id || '',
          conversation_id: ctx.conversation_id || '',
        })
      );
    };

    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }

      if (msg.type === 'conversation' && msg.conversation) {
        window.FreeAIHub?.onVoiceConversationCreated?.(msg.conversation);
      } else if (msg.type === 'session_ready') {
        voiceState.engine = msg.engine;
        voiceState.hasServerStt = msg.has_server_stt !== false;
        vEls.engineBadge.textContent =
          msg.engine === 'gemini_live' ? 'Gemini 3.8 Live' : 'Universal Free Voice';
        vEls.modelBadge.textContent = msg.label || 'Ready';
        startMicrophone();
      } else if (msg.type === 'engine_fallback') {
        voiceState.engine = msg.engine;
        vEls.engineBadge.textContent = 'Universal Free Voice';
        window.FreeAIHub?.toast(msg.reason, 'info');
      } else if (msg.type === 'meta') {
        vEls.modelBadge.textContent = `${msg.model_label} (${msg.route_reason})`;
      } else if (msg.type === 'status') {
        if (msg.state === 'transcribing') {
          setVisualState('thinking', 'কথা প্রসেস করছি...');
        }
      } else if (msg.type === 'transcript') {
        appendTranscript(msg.role, msg.text, !msg.final || msg.role === 'assistant');
        if (msg.final && msg.role === 'user') {
          voiceState.assistantLineEl = null;
          setVisualState('thinking', 'উত্তর তৈরি করছি...');
        }
      } else if (msg.type === 'audio_pcm') {
        playPcm24k(msg.data, msg.sample_rate || 24000);
      } else if (msg.type === 'audio_mp3') {
        playMp3(msg.data);
      } else if (msg.type === 'interrupted') {
        stopPlayback();
        voiceState.assistantLineEl = null;
        if (voiceState.isRecording) {
          setVisualState('listening', 'শুনছি...');
        }
      } else if (msg.type === 'turn_complete') {
        voiceState.userLineEl = null;
        voiceState.assistantLineEl = null;
        if (!voiceState.isSpeaking) {
          setVisualState(
            voiceState.isRecording ? 'listening' : 'idle',
            voiceState.isRecording ? 'শুনছি...' : 'কথা বলতে ট্যাপ করুন'
          );
        }
      } else if (msg.type === 'error') {
        window.FreeAIHub?.toast(msg.message, 'error');
        setVisualState('idle', 'সমস্যা হয়েছে', msg.message);
      }
    };

    ws.onerror = () => {
      window.FreeAIHub?.toast('ভয়েস ওয়েবসকেট সংযোগে সমস্যা হয়েছে।', 'error');
      setVisualState('idle', 'সংযোগ বিচ্ছিন্ন হয়েছে');
    };

    ws.onclose = () => {
      stopMicrophone(false);
    };
  }

  function open() {
    ensurePlaybackContext();
    voiceState.isOpen = true;
    vEls.overlay.classList.remove('hidden');
    vEls.overlay.setAttribute('aria-hidden', 'false');
    vEls.transcriptBox.innerHTML = '';
    vEls.micNotice.classList.add('hidden');
    setVisualState('thinking', 'কানেক্ট হচ্ছে...');
    connectSocket();
  }

  function close() {
    voiceState.isOpen = false;
    stopMicrophone(false);
    stopPlayback();
    if (voiceState.ws) {
      try {
        if (voiceState.ws.readyState === 1) {
          voiceState.ws.send(JSON.stringify({ type: 'stop' }));
        }
        voiceState.ws.close();
      } catch {}
      voiceState.ws = null;
    }
    vEls.overlay.classList.add('hidden');
    vEls.overlay.setAttribute('aria-hidden', 'true');
    window.FreeAIHub?.onVoiceTurnSaved?.();
  }

  function syncSettings(settings) {
    if (settings.voice_engine) vEls.engineSelect.value = settings.voice_engine;
    if (settings.voice_language) vEls.langSelect.value = settings.voice_language;
    if (settings.voice_gender) vEls.genderSelect.value = settings.voice_gender;
  }

  function bindVoiceEvents() {
    vEls.closeBtn.addEventListener('click', close);
    vEls.endBtn.addEventListener('click', close);

    vEls.mainBtn.addEventListener('click', () => {
      ensurePlaybackContext();
      if (voiceState.isRecording) {
        stopMicrophone(voiceState.vadMode === 'ptt');
      } else {
        startMicrophone();
      }
    });

    vEls.vadModeBtn.addEventListener('click', () => {
      voiceState.vadMode = voiceState.vadMode === 'handsfree' ? 'ptt' : 'handsfree';
      vEls.vadModeBtn.textContent =
        voiceState.vadMode === 'handsfree' ? 'Hands-Free (Auto)' : 'Push-to-Talk';
      if (voiceState.isRecording) {
        vEls.mainBtn.textContent =
          voiceState.vadMode === 'ptt' ? '📤 পাঠানো শেষ করুন' : '⏸ মাইক মিউট করুন';
      }
    });

    vEls.interruptBtn.addEventListener('click', () => {
      stopPlayback();
      if (voiceState.ws?.readyState === 1) {
        voiceState.ws.send(JSON.stringify({ type: 'interrupt' }));
      }
    });

    const reconnectWithConfig = () => {
      if (!voiceState.isOpen) return;
      stopMicrophone(false);
      stopPlayback();
      if (voiceState.ws) {
        voiceState.ws.close();
        voiceState.ws = null;
      }
      connectSocket();
    };

    vEls.engineSelect.addEventListener('change', reconnectWithConfig);
    vEls.langSelect.addEventListener('change', () => {
      if (voiceState.engine === 'gemini_live') {
        reconnectWithConfig();
      } else if (voiceState.ws?.readyState === 1) {
        voiceState.ws.send(
          JSON.stringify({
            type: 'config',
            language: vEls.langSelect.value,
            gender: vEls.genderSelect.value,
          })
        );
      }
    });
    vEls.genderSelect.addEventListener('change', () => {
      if (voiceState.ws?.readyState === 1) {
        voiceState.ws.send(
          JSON.stringify({
            type: 'config',
            language: vEls.langSelect.value,
            gender: vEls.genderSelect.value,
          })
        );
      }
    });

    vEls.textForm.addEventListener('submit', (e) => {
      e.preventDefault();
      ensurePlaybackContext();
      const txt = vEls.textInput.value.trim();
      if (!txt || !voiceState.ws || voiceState.ws.readyState !== 1) return;
      stopPlayback();
      vEls.textInput.value = '';
      voiceState.ws.send(JSON.stringify({ type: 'text', text: txt }));
      setVisualState('thinking', 'উত্তর তৈরি হচ্ছে...');
    });
  }

  document.addEventListener('DOMContentLoaded', bindVoiceEvents);
  window.FreeAIHubVoice = { open, close, syncSettings };
})();
