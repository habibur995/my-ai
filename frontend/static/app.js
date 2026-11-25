(() => {
  'use strict';

  const state = {
    pin: sessionStorage.getItem('freeai_pin') || '',
    settings: {},
    providers: [],
    conversations: [],
    activeConvId: '',
    messages: [],
    mode: 'auto', // 'auto' | 'manual'
    selectedProvider: '',
    selectedModel: '',
    compareMode: false,
    attachments: [],
    streamingAbort: null,
    speakingAudio: null,
    network: { port: 8000, lan_ips: [] },
  };

  const $ = (id) => document.getElementById(id);

  const els = {
    pinOverlay: $('pinLockOverlay'),
    pinForm: $('pinForm'),
    pinInput: $('pinInput'),
    pinError: $('pinError'),
    sidebar: $('sidebar'),
    sidebarBackdrop: $('sidebarBackdrop'),
    sidebarCloseBtn: $('sidebarCloseBtn'),
    sidebarToggleBtn: $('sidebarToggleBtn'),
    newChatBtn: $('newChatBtn'),
    convSearchInput: $('convSearchInput'),
    conversationList: $('conversationList'),
    sidebarProviderSummary: $('sidebarProviderSummary'),
    openSettingsBtn: $('openSettingsBtn'),
    openMobileBtn: $('openMobileBtn'),
    topSettingsBtn: $('topSettingsBtn'),
    modelPickerBtn: $('modelPickerBtn'),
    modelPickerDot: $('modelPickerDot'),
    modelPickerLabel: $('modelPickerLabel'),
    modelPickerMenu: $('modelPickerMenu'),
    compareToggleBtn: $('compareToggleBtn'),
    compareBar: $('compareBar'),
    compareModelA: $('compareModelA'),
    compareModelB: $('compareModelB'),
    openVoiceBtn: $('openVoiceBtn'),
    composerVoiceBtn: $('composerVoiceBtn'),
    themeToggleBtn: $('themeToggleBtn'),
    chatScroll: $('chatScroll'),
    emptyState: $('emptyState'),
    onboardingBanner: $('onboardingBanner'),
    onboardingConnectBtn: $('onboardingConnectBtn'),
    messagesContainer: $('messagesContainer'),
    attachmentStrip: $('attachmentStrip'),
    fileInput: $('fileInput'),
    attachBtn: $('attachBtn'),
    promptInput: $('promptInput'),
    sendBtn: $('sendBtn'),
    sendIcon: $('sendIcon'),
    stopIcon: $('stopIcon'),
    composerRouteHint: $('composerRouteHint'),
    settingsModal: $('settingsModal'),
    closeSettingsBtn: $('closeSettingsBtn'),
    providersList: $('providersList'),
    prefSystemPrompt: $('prefSystemPrompt'),
    prefDefaultMode: $('prefDefaultMode'),
    prefAutoFallback: $('prefAutoFallback'),
    prefVoiceEngine: $('prefVoiceEngine'),
    prefVoiceLang: $('prefVoiceLang'),
    prefVoiceGender: $('prefVoiceGender'),
    prefPinStatus: $('prefPinStatus'),
    prefNewPin: $('prefNewPin'),
    savePinBtn: $('savePinBtn'),
    clearPinBtn: $('clearPinBtn'),
    savePrefsBtn: $('savePrefsBtn'),
    lanUrlsList: $('lanUrlsList'),
    toastContainer: $('toastContainer'),
  };

  function toast(message, kind = 'info') {
    const div = document.createElement('div');
    div.className = `toast ${kind}`;
    div.textContent = message;
    els.toastContainer.appendChild(div);
    setTimeout(() => div.remove(), 4200);
  }

  function escapeHtml(text) {
    return String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function sanitizeHtml(html) {
    const tmpl = document.createElement('template');
    tmpl.innerHTML = html;
    tmpl.content.querySelectorAll('script, iframe, object, embed').forEach((el) => el.remove());
    tmpl.content.querySelectorAll('*').forEach((el) => {
      for (const attr of Array.from(el.attributes)) {
        if (attr.name.toLowerCase().startsWith('on')) {
          el.removeAttribute(attr.name);
        } else if (
          (attr.name === 'href' || attr.name === 'src') &&
          attr.value.trim().toLowerCase().startsWith('javascript:')
        ) {
          el.removeAttribute(attr.name);
        }
      }
    });
    return tmpl.innerHTML;
  }

  function renderMarkdown(md) {
    const raw = String(md || '');
    if (window.marked && typeof window.marked.parse === 'function') {
      const html = window.marked.parse(raw, { breaks: true, gfm: true });
      return sanitizeHtml(html);
    }
    return `<p>${escapeHtml(raw).replace(/\n/g, '<br/>')}</p>`;
  }

  function enhanceCodeBlocks(container) {
    container.querySelectorAll('pre').forEach((pre) => {
      if (pre.querySelector('.code-header')) return;
      const code = pre.querySelector('code');
      let lang = 'text';
      if (code) {
        const cls = Array.from(code.classList).find((c) => c.startsWith('language-'));
        if (cls) lang = cls.replace('language-', '');
        if (window.hljs && typeof window.hljs.highlightElement === 'function') {
          try {
            window.hljs.highlightElement(code);
          } catch {}
        }
      }
      const header = document.createElement('div');
      header.className = 'code-header';
      header.innerHTML = `<span>${escapeHtml(lang)}</span>`;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'code-copy-btn';
      btn.textContent = 'Copy';
      btn.addEventListener('click', async () => {
        const txt = code ? code.textContent : pre.textContent;
        await navigator.clipboard.writeText(txt || '');
        btn.textContent = 'Copied!';
        setTimeout(() => (btn.textContent = 'Copy'), 1500);
      });
      header.appendChild(btn);
      pre.prepend(header);
    });
  }

  async function apiFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (state.pin) {
      headers.set('X-Access-Pin', state.pin);
    }
    const resp = await fetch(url, { ...options, headers });
    if (resp.status === 401) {
      state.pin = '';
      sessionStorage.removeItem('freeai_pin');
      showPinOverlay();
      throw new Error('PIN required');
    }
    return resp;
  }

  function showPinOverlay() {
    els.pinOverlay.classList.remove('hidden');
    els.pinOverlay.setAttribute('aria-hidden', 'false');
    els.pinInput.focus();
  }

  function hidePinOverlay() {
    els.pinOverlay.classList.add('hidden');
    els.pinOverlay.setAttribute('aria-hidden', 'true');
    els.pinError.classList.add('hidden');
  }

  function getConnectedModelsFlat() {
    const items = [];
    for (const p of state.providers) {
      if (!p.connected || !p.enabled) continue;
      for (const m of p.models || []) {
        items.push({ provider: p, model: m });
      }
    }
    return items;
  }

  function findProvider(providerId) {
    return state.providers.find((p) => p.id === providerId);
  }

  function applyTheme(theme) {
    const t = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', t);
    state.settings.theme = t;
  }

  function updateProviderSummary() {
    const connected = state.providers.filter((p) => p.connected && p.enabled);
    const totalModels = connected.reduce((acc, p) => acc + (p.models?.length || 0), 0);
    if (connected.length === 0) {
      els.sidebarProviderSummary.innerHTML = `<span class="dot offline"></span><span>কোনো ফ্রি API যুক্ত নেই</span>`;
      els.onboardingBanner.classList.remove('hidden');
    } else {
      els.sidebarProviderSummary.innerHTML = `<span class="dot online"></span><span>${connected.length} টি API · ${totalModels} ফ্রি মডেল</span>`;
      els.onboardingBanner.classList.add('hidden');
    }
  }

  function renderModelPicker() {
    const items = getConnectedModelsFlat();
    if (state.mode === 'auto' || !state.selectedModel) {
      els.modelPickerDot.className = 'model-dot auto';
      els.modelPickerDot.style.background = '';
      els.modelPickerLabel.textContent = '✨ Auto (Smart Choose)';
      els.composerRouteHint.textContent = '✨ Auto Mode · কমান্ড বুঝে সেরা ফ্রি মডেল ও 429 ফলব্যাক সক্রিয়';
    } else {
      const prov = findProvider(state.selectedProvider);
      const found = items.find(
        (x) => x.provider.id === state.selectedProvider && x.model.id === state.selectedModel
      );
      els.modelPickerDot.className = 'model-dot';
      els.modelPickerDot.style.background = prov ? prov.color : '#6366f1';
      els.modelPickerLabel.textContent = found ? found.model.label : state.selectedModel;
      els.composerRouteHint.textContent = `Manual: ${prov?.name || state.selectedProvider} · 429 হলে অটো-ফলব্যাক ${state.settings.auto_fallback !== 'false' ? 'চালু' : 'বন্ধ'}`;
    }

    const menu = els.modelPickerMenu;
    menu.innerHTML = '';

    const autoBtn = document.createElement('button');
    autoBtn.type = 'button';
    autoBtn.className = `picker-option ${state.mode === 'auto' ? 'selected' : ''}`;
    autoBtn.innerHTML = `
      <div>
        <div><strong>✨ Auto (Smart Choose)</strong></div>
        <div class="muted-small">Coding, Vision, Bangla ও 429 fallback</div>
      </div>
      <span class="tag-chip">Best</span>
    `;
    autoBtn.addEventListener('click', () => {
      state.mode = 'auto';
      state.selectedProvider = '';
      state.selectedModel = '';
      menu.classList.add('hidden');
      renderModelPicker();
    });
    menu.appendChild(autoBtn);

    const connectedProviders = state.providers.filter((p) => p.connected && p.enabled && (p.models || []).length > 0);
    if (connectedProviders.length === 0) {
      const emptyHint = document.createElement('div');
      emptyHint.className = 'muted-small';
      emptyHint.style.padding = '10px';
      emptyHint.textContent = 'ফ্রি মডেল দেখতে Settings থেকে অন্তত ১টি API key যুক্ত করুন।';
      menu.appendChild(emptyHint);
    } else {
      for (const p of connectedProviders) {
        const grp = document.createElement('div');
        grp.className = 'picker-group-title';
        grp.innerHTML = `<span class="model-dot" style="background:${p.color}"></span><span>${escapeHtml(p.name)}</span>`;
        menu.appendChild(grp);

        for (const m of p.models) {
          const btn = document.createElement('button');
          btn.type = 'button';
          const isSel =
            state.mode === 'manual' &&
            state.selectedProvider === p.id &&
            state.selectedModel === m.id;
          btn.className = `picker-option ${isSel ? 'selected' : ''}`;
          const tagsHtml = (m.tags || [])
            .slice(0, 3)
            .map((t) => `<span class="tag-chip">${escapeHtml(t)}</span>`)
            .join('');
          btn.innerHTML = `<span>${escapeHtml(m.label)}</span><span class="picker-tags">${tagsHtml}</span>`;
          btn.addEventListener('click', () => {
            state.mode = 'manual';
            state.selectedProvider = p.id;
            state.selectedModel = m.id;
            menu.classList.add('hidden');
            renderModelPicker();
          });
          menu.appendChild(btn);
        }
      }
    }

    // Also refresh Compare dropdowns
    const optionsHtml = items
      .map(
        ({ provider, model }) =>
          `<option value="${escapeHtml(provider.id)}::${escapeHtml(model.id)}">[${escapeHtml(provider.badge)}] ${escapeHtml(model.label)}</option>`
      )
      .join('');
    const prevA = els.compareModelA.value;
    const prevB = els.compareModelB.value;
    els.compareModelA.innerHTML = optionsHtml || '<option value="">No models connected</option>';
    els.compareModelB.innerHTML = optionsHtml || '<option value="">No models connected</option>';
    if (prevA) els.compareModelA.value = prevA;
    if (prevB) {
      els.compareModelB.value = prevB;
    } else if (els.compareModelB.options.length > 1) {
      els.compareModelB.selectedIndex = 1;
    }
  }

  function renderConversations() {
    const list = els.conversationList;
    list.innerHTML = '';
    if (!state.conversations.length) {
      list.innerHTML = `<div class="muted-small" style="padding: 12px 10px;">এখনো কোনো চ্যাট নেই।</div>`;
      return;
    }
    for (const c of state.conversations) {
      const row = document.createElement('div');
      row.className = `conv-item ${c.id === state.activeConvId ? 'active' : ''}`;
      row.innerHTML = `
        <span class="conv-title">${c.pinned ? '📌 ' : ''}${escapeHtml(c.title)}</span>
        <div class="conv-actions">
          <button type="button" class="conv-act-btn" title="Pin">${c.pinned ? '★' : '☆'}</button>
          <button type="button" class="conv-act-btn" title="Rename">✎</button>
          <button type="button" class="conv-act-btn" title="Delete">🗑</button>
        </div>
      `;
      row.addEventListener('click', (e) => {
        if (e.target.closest('.conv-actions')) return;
        loadConversation(c.id);
        closeMobileSidebar();
      });

      const [pinBtn, renBtn, delBtn] = row.querySelectorAll('.conv-act-btn');
      pinBtn.addEventListener('click', async () => {
        await apiFetch(`/api/conversations/${c.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pinned: !c.pinned }),
        });
        await refreshConversations();
      });
      renBtn.addEventListener('click', async () => {
        const nextTitle = window.prompt('চ্যাটের নতুন নাম লিখুন:', c.title);
        if (!nextTitle || !nextTitle.trim()) return;
        await apiFetch(`/api/conversations/${c.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: nextTitle.trim() }),
        });
        await refreshConversations();
      });
      delBtn.addEventListener('click', async () => {
        await apiFetch(`/api/conversations/${c.id}`, { method: 'DELETE' });
        if (state.activeConvId === c.id) {
          startNewChat();
        }
        await refreshConversations();
      });
      list.appendChild(row);
    }
  }

  async function refreshConversations(query = '') {
    const url = query
      ? `/api/conversations?q=${encodeURIComponent(query)}`
      : '/api/conversations';
    const resp = await apiFetch(url);
    state.conversations = await resp.json();
    renderConversations();
  }

  async function loadConversation(convId) {
    state.activeConvId = convId;
    const resp = await apiFetch(`/api/conversations/${convId}/messages`);
    state.messages = await resp.json();
    renderMessages();
    renderConversations();
  }

  function startNewChat() {
    state.activeConvId = '';
    state.messages = [];
    renderMessages();
    renderConversations();
    els.promptInput.focus();
  }

  function createAssistantFooter(msg, isLastAssistant) {
    const prov = findProvider(msg.provider_id);
    const dotColor = prov ? prov.color : '#6366f1';
    const footer = document.createElement('div');
    footer.className = 'msg-footer';

    const badge = document.createElement('span');
    badge.className = 'model-badge';
    badge.innerHTML = `<span class="model-dot" style="background:${dotColor}"></span><span>${escapeHtml(msg.model_id || 'AI')}</span>`;
    footer.appendChild(badge);

    if (msg.route_reason) {
      const route = document.createElement('span');
      route.textContent = msg.route_reason;
      footer.appendChild(route);
    }

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'msg-act-btn';
    copyBtn.textContent = '📋 Copy';
    copyBtn.addEventListener('click', async () => {
      await navigator.clipboard.writeText(msg.content || '');
      copyBtn.textContent = '✓ Copied';
      setTimeout(() => (copyBtn.textContent = '📋 Copy'), 1500);
    });
    footer.appendChild(copyBtn);

    const speakBtn = document.createElement('button');
    speakBtn.type = 'button';
    speakBtn.className = 'msg-act-btn';
    speakBtn.textContent = '🔊 শুনুন';
    speakBtn.addEventListener('click', () => speakText(msg.content || '', speakBtn));
    footer.appendChild(speakBtn);

    if (isLastAssistant) {
      const regenBtn = document.createElement('button');
      regenBtn.type = 'button';
      regenBtn.className = 'msg-act-btn';
      regenBtn.textContent = '↻ Regenerate';
      regenBtn.addEventListener('click', () => sendMessage({ regenerate: true }));
      footer.appendChild(regenBtn);
    }

    return footer;
  }

  async function speakText(text, btn) {
    if (state.speakingAudio) {
      state.speakingAudio.pause();
      state.speakingAudio = null;
      btn.textContent = '🔊 শুনুন';
      return;
    }
    const orig = btn.textContent;
    btn.textContent = '⏳...';
    try {
      const resp = await apiFetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          language: state.settings.voice_language || 'auto',
          gender: state.settings.voice_gender || 'female',
        }),
      });
      if (!resp.ok) throw new Error('TTS failed');
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      state.speakingAudio = audio;
      btn.textContent = '⏹ থামান';
      audio.onended = () => {
        URL.revokeObjectURL(url);
        state.speakingAudio = null;
        btn.textContent = orig;
      };
      await audio.play();
    } catch (err) {
      btn.textContent = orig;
      toast(`অডিও প্লে সমস্যা: ${err.message}`, 'error');
    }
  }

  function renderMessages() {
    const container = els.messagesContainer;
    container.innerHTML = '';

    if (!state.messages.length) {
      els.emptyState.classList.remove('hidden');
      return;
    }
    els.emptyState.classList.add('hidden');

    let lastAssistantIdx = -1;
    state.messages.forEach((m, i) => {
      if (m.role === 'assistant') lastAssistantIdx = i;
    });

    state.messages.forEach((msg, idx) => {
      const row = document.createElement('div');
      row.className = `msg-row ${msg.role}`;

      if (msg.role === 'user') {
        const bubble = document.createElement('div');
        bubble.className = 'msg-bubble user';

        if (msg.attachments && msg.attachments.length) {
          const attWrap = document.createElement('div');
          attWrap.className = 'msg-attachments';
          for (const a of msg.attachments) {
            if (a.mime_type && a.mime_type.startsWith('image/') && a.data) {
              const img = document.createElement('img');
              img.className = 'msg-img-thumb';
              img.src = `data:${a.mime_type};base64,${a.data}`;
              img.alt = a.name || 'image';
              attWrap.appendChild(img);
            } else {
              const pill = document.createElement('span');
              pill.className = 'msg-file-pill';
              pill.textContent = `📎 ${a.name || 'file'}`;
              attWrap.appendChild(pill);
            }
          }
          bubble.appendChild(attWrap);
        }

        const txt = document.createElement('div');
        txt.textContent = msg.content || '';
        bubble.appendChild(txt);
        row.appendChild(bubble);
      } else {
        const bubble = document.createElement('div');
        bubble.className = 'msg-bubble assistant';
        bubble.innerHTML = renderMarkdown(msg.content || '');
        enhanceCodeBlocks(bubble);
        row.appendChild(bubble);
        row.appendChild(createAssistantFooter(msg, idx === lastAssistantIdx));
      }
      container.appendChild(row);
    });

    els.chatScroll.scrollTop = els.chatScroll.scrollHeight;
  }

  function setStreamingUi(isStreaming) {
    if (isStreaming) {
      els.sendBtn.classList.add('streaming');
      els.sendIcon.classList.add('hidden');
      els.stopIcon.classList.remove('hidden');
      els.sendBtn.title = 'থামান';
    } else {
      els.sendBtn.classList.remove('streaming');
      els.sendIcon.classList.remove('hidden');
      els.stopIcon.classList.add('hidden');
      els.sendBtn.title = 'পাঠান (Enter)';
    }
  }

  async function sendMessage({ regenerate = false, overridePrompt = '' } = {}) {
    if (state.streamingAbort) {
      state.streamingAbort.abort();
      state.streamingAbort = null;
      setStreamingUi(false);
      return;
    }

    const promptText = overridePrompt || els.promptInput.value.trim();
    if (!regenerate && !promptText && state.attachments.length === 0) return;

    if (state.compareMode && !regenerate) {
      return sendCompareMessage(promptText);
    }

    const attachmentsToSend = regenerate ? [] : [...state.attachments];
    if (!regenerate) {
      els.promptInput.value = '';
      els.promptInput.style.height = 'auto';
      state.attachments = [];
      renderAttachments();
      state.messages.push({
        role: 'user',
        content: promptText,
        attachments: attachmentsToSend,
      });
    } else if (state.messages.length && state.messages[state.messages.length - 1].role === 'assistant') {
      state.messages.pop();
    }

    const assistantDraft = {
      role: 'assistant',
      content: '',
      provider_id: '',
      model_id: '',
      route_reason: '',
    };
    state.messages.push(assistantDraft);
    renderMessages();

    const lastBubble = els.messagesContainer.lastElementChild?.querySelector('.msg-bubble.assistant');
    const lastFooter = els.messagesContainer.lastElementChild?.querySelector('.msg-footer');
    if (lastBubble) lastBubble.classList.add('typing-cursor');

    const controller = new AbortController();
    state.streamingAbort = controller;
    setStreamingUi(true);

    try {
      const resp = await apiFetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          conversation_id: state.activeConvId,
          prompt: promptText,
          attachments: attachmentsToSend,
          mode: state.mode,
          provider_id: state.selectedProvider,
          model_id: state.selectedModel,
          regenerate,
        }),
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() || '';

        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line.startsWith('data:')) continue;
          const payloadStr = line.slice(5).trim();
          if (!payloadStr) continue;
          let ev;
          try {
            ev = JSON.parse(payloadStr);
          } catch {
            continue;
          }

          if (ev.type === 'conversation' && ev.conversation) {
            state.activeConvId = ev.conversation.id;
            refreshConversations(els.convSearchInput.value.trim());
          } else if (ev.type === 'meta') {
            assistantDraft.provider_id = ev.provider_id;
            assistantDraft.model_id = ev.model_id;
            assistantDraft.route_reason = ev.route_reason;
            if (ev.fallback_used) {
              toast(`রেট লিমিট বা সমস্যার কারণে অটো-ফলব্যাক: ${ev.model_label}`, 'info');
            }
            if (lastFooter) {
              lastFooter.replaceWith(createAssistantFooter(assistantDraft, true));
            }
          } else if (ev.type === 'delta') {
            assistantDraft.content += ev.text;
            if (lastBubble) {
              lastBubble.innerHTML = renderMarkdown(assistantDraft.content);
              enhanceCodeBlocks(lastBubble);
            }
            els.chatScroll.scrollTop = els.chatScroll.scrollHeight;
          } else if (ev.type === 'error') {
            assistantDraft.content = `⚠️ ${ev.message}`;
            if (lastBubble) {
              lastBubble.innerHTML = renderMarkdown(assistantDraft.content);
            }
            toast(ev.message, 'error');
          } else if (ev.type === 'done') {
            if (ev.message) {
              Object.assign(assistantDraft, ev.message);
            }
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        toast('উত্তর জেনারেট করা থামানো হয়েছে।', 'info');
      } else {
        assistantDraft.content = `⚠️ ${err.message}`;
        toast(err.message, 'error');
      }
    } finally {
      if (lastBubble) lastBubble.classList.remove('typing-cursor');
      state.streamingAbort = null;
      setStreamingUi(false);
      renderMessages();
      refreshConversations(els.convSearchInput.value.trim());
    }
  }

  async function sendCompareMessage(promptText) {
    const valA = els.compareModelA.value;
    const valB = els.compareModelB.value;
    if (!valA || !valB) {
      toast('Compare মোডের জন্য অন্তত দুটি ফ্রি মডেল সিলেক্ট করুন।', 'error');
      return;
    }
    const [pidA, midA] = valA.split('::');
    const [pidB, midB] = valB.split('::');

    const atts = [...state.attachments];
    els.promptInput.value = '';
    state.attachments = [];
    renderAttachments();

    state.messages.push({ role: 'user', content: promptText, attachments: atts });
    renderMessages();

    const compareRow = document.createElement('div');
    compareRow.className = 'msg-row assistant';
    compareRow.innerHTML = `
      <div class="compare-grid">
        <div class="compare-card" id="cmpCardA">
          <div class="msg-footer"><span class="model-badge">${escapeHtml(midA)}</span><span>Model A</span></div>
          <div class="msg-bubble assistant typing-cursor"></div>
        </div>
        <div class="compare-card" id="cmpCardB">
          <div class="msg-footer"><span class="model-badge">${escapeHtml(midB)}</span><span>Model B</span></div>
          <div class="msg-bubble assistant typing-cursor"></div>
        </div>
      </div>
    `;
    els.messagesContainer.appendChild(compareRow);
    els.chatScroll.scrollTop = els.chatScroll.scrollHeight;

    const controller = new AbortController();
    state.streamingAbort = controller;
    setStreamingUi(true);

    const textAcc = { A: '', B: '' };
    const bubbleA = compareRow.querySelector('#cmpCardA .msg-bubble');
    const bubbleB = compareRow.querySelector('#cmpCardB .msg-bubble');

    try {
      const resp = await apiFetch('/api/chat/compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          conversation_id: state.activeConvId,
          prompt: promptText,
          attachments: atts,
          slot_a: { provider_id: pidA, model_id: midA },
          slot_b: { provider_id: pidB, model_id: midB },
        }),
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() || '';
        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line.startsWith('data:')) continue;
          let ev;
          try {
            ev = JSON.parse(line.slice(5).trim());
          } catch {
            continue;
          }
          if (ev.type === 'conversation' && ev.conversation) {
            state.activeConvId = ev.conversation.id;
          } else if (ev.type === 'delta') {
            textAcc[ev.slot] += ev.text;
            const target = ev.slot === 'A' ? bubbleA : bubbleB;
            target.innerHTML = renderMarkdown(textAcc[ev.slot]);
            enhanceCodeBlocks(target);
            els.chatScroll.scrollTop = els.chatScroll.scrollHeight;
          } else if (ev.type === 'slot_error') {
            const target = ev.slot === 'A' ? bubbleA : bubbleB;
            target.innerHTML = renderMarkdown(`⚠️ ${ev.message}`);
            target.classList.remove('typing-cursor');
          } else if (ev.type === 'slot_done') {
            const target = ev.slot === 'A' ? bubbleA : bubbleB;
            target.classList.remove('typing-cursor');
          }
        }
      }
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      bubbleA.classList.remove('typing-cursor');
      bubbleB.classList.remove('typing-cursor');
      state.streamingAbort = null;
      setStreamingUi(false);
      if (state.activeConvId) {
        await loadConversation(state.activeConvId);
        await refreshConversations();
      }
    }
  }

  function renderAttachments() {
    const strip = els.attachmentStrip;
    strip.innerHTML = '';
    if (!state.attachments.length) {
      strip.classList.add('hidden');
      return;
    }
    strip.classList.remove('hidden');
    state.attachments.forEach((att, idx) => {
      const chip = document.createElement('div');
      chip.className = 'att-chip';
      if (att.mime_type.startsWith('image/')) {
        chip.innerHTML = `<img src="data:${att.mime_type};base64,${att.data}" alt=""/><span>${escapeHtml(att.name)}</span>`;
      } else {
        chip.innerHTML = `<span>📄 ${escapeHtml(att.name)}</span>`;
      }
      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'att-remove';
      rm.textContent = '✕';
      rm.addEventListener('click', () => {
        state.attachments.splice(idx, 1);
        renderAttachments();
      });
      chip.appendChild(rm);
      strip.appendChild(chip);
    });
  }

  async function handleFilesSelected(files) {
    for (const file of Array.from(files || [])) {
      if (file.size > 6 * 1024 * 1024) {
        toast(`${file.name} ফাইলটি 6MB-এর বেশি — ছোট ফাইল দিন।`, 'error');
        continue;
      }
      if (file.type.startsWith('image/')) {
        const b64 = await new Promise((resolve) => {
          const reader = new FileReader();
          reader.onload = () => {
            const res = String(reader.result || '');
            resolve(res.split(',')[1] || '');
          };
          reader.readAsDataURL(file);
        });
        state.attachments.push({ name: file.name, mime_type: file.type, data: b64 });
      } else {
        const textContent = await file.text();
        state.attachments.push({
          name: file.name,
          mime_type: file.type || 'text/plain',
          text_content: textContent.slice(0, 45000),
        });
      }
    }
    renderAttachments();
  }

  function renderProvidersSettings() {
    const grid = els.providersList;
    grid.innerHTML = '';
    for (const p of state.providers) {
      const card = document.createElement('div');
      card.className = 'provider-card';

      const statusClass = p.status === 'ok' ? 'ok' : p.status === 'error' || p.status === 'auth_error' ? 'error' : '';
      const statusText = p.status_message || (p.connected ? 'Connected' : 'যুক্ত করা হয়নি');
      const modelsMini = (p.models || [])
        .slice(0, 6)
        .map((m) => `<span class="tag-chip">${escapeHtml(m.label)}</span>`)
        .join('');

      card.innerHTML = `
        <div class="provider-card-head">
          <div>
            <div class="provider-title-row">
              <span class="model-dot" style="background:${p.color}"></span>
              <span>${escapeHtml(p.name)}</span>
            </div>
            <div class="provider-note">${escapeHtml(p.free_note_bn || p.free_note)}</div>
          </div>
          <a class="provider-link" href="${escapeHtml(p.signup_url)}" target="_blank" rel="noopener">Get Free Key ↗</a>
        </div>
        <div class="provider-inputs">
          ${p.requires_key ? `<input type="password" class="p-key-input" placeholder="${p.has_key ? `সংরক্ষিত (${escapeHtml(p.masked_key)}) — নতুন দিতে লিখুন` : 'ফ্রি API Key এখানে পেস্ট করুন...'}" autocomplete="off" />` : ''}
          ${p.id === 'ollama' ? `<input type="text" class="p-url-input" value="${escapeHtml(p.base_url)}" placeholder="http://127.0.0.1:11434/v1" />` : ''}
        </div>
        <div class="provider-actions">
          <button type="button" class="btn-primary p-save-btn">${p.requires_key ? 'Test & Save' : 'Connect & Scan'}</button>
          <button type="button" class="btn-secondary p-refresh-btn" ${p.has_key || p.id === 'ollama' ? '' : 'disabled'}>Refresh</button>
          ${p.has_key || p.connected ? `<button type="button" class="btn-danger-outline p-del-btn">Remove</button>` : ''}
        </div>
        <div class="provider-status ${statusClass}">${escapeHtml(statusText)}</div>
        ${modelsMini ? `<div class="provider-models-mini">${modelsMini}${(p.models?.length || 0) > 6 ? `<span class="tag-chip">+${p.models.length - 6} more</span>` : ''}</div>` : ''}
      `;

      const keyInput = card.querySelector('.p-key-input');
      const urlInput = card.querySelector('.p-url-input');
      const saveBtn = card.querySelector('.p-save-btn');
      const refreshBtn = card.querySelector('.p-refresh-btn');
      const delBtn = card.querySelector('.p-del-btn');

      saveBtn.addEventListener('click', async () => {
        saveBtn.disabled = true;
        const orig = saveBtn.textContent;
        saveBtn.textContent = 'Testing...';
        try {
          const payload = { test: true };
          if (keyInput && keyInput.value.trim()) {
            payload.api_key = keyInput.value.trim();
          }
          if (urlInput && urlInput.value.trim()) {
            payload.base_url = urlInput.value.trim();
          }
          const resp = await apiFetch(`/api/providers/${p.id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const res = await resp.json();
          toast(res.message || 'Updated', res.ok ? 'success' : 'error');
          await reloadProviders();
        } catch (err) {
          toast(err.message, 'error');
        } finally {
          saveBtn.disabled = false;
          saveBtn.textContent = orig;
        }
      });

      refreshBtn.addEventListener('click', async () => {
        refreshBtn.disabled = true;
        try {
          const resp = await apiFetch(`/api/providers/${p.id}/refresh`, { method: 'POST' });
          const res = await resp.json();
          toast(res.message, res.ok ? 'success' : 'error');
          await reloadProviders();
        } finally {
          refreshBtn.disabled = false;
        }
      });

      if (delBtn) {
        delBtn.addEventListener('click', async () => {
          await apiFetch(`/api/providers/${p.id}`, { method: 'DELETE' });
          toast(`${p.name} রিমুভ করা হয়েছে`, 'info');
          await reloadProviders();
        });
      }

      grid.appendChild(card);
    }
  }

  function renderMobileTab() {
    const port = state.network?.port || 8000;
    const ips = state.network?.lan_ips || [];
    if (!ips.length) {
      els.lanUrlsList.innerHTML = `<span class="muted-small">স্থানীয় Wi-Fi IP পাওয়া যায়নি।</span>`;
      return;
    }
    els.lanUrlsList.innerHTML = '';
    for (const ip of ips) {
      const url = `http://${ip}:${port}`;
      const pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'lan-pill';
      pill.textContent = url;
      pill.title = 'Click to copy';
      pill.addEventListener('click', async () => {
        await navigator.clipboard.writeText(url);
        toast(`কপি হয়েছে: ${url}`, 'success');
      });
      els.lanUrlsList.appendChild(pill);
    }
  }

  function renderPreferencesTab() {
    els.prefSystemPrompt.value = state.settings.system_prompt || '';
    els.prefDefaultMode.value = state.settings.default_mode || 'auto';
    els.prefAutoFallback.value = state.settings.auto_fallback || 'true';
    els.prefVoiceEngine.value = state.settings.voice_engine || 'auto';
    els.prefVoiceLang.value = state.settings.voice_language || 'auto';
    els.prefVoiceGender.value = state.settings.voice_gender || 'female';

    const pinOn = Boolean(state.settings.pin_enabled);
    els.prefPinStatus.textContent = pinOn
      ? 'PIN লক সক্রিয় আছে। নতুন PIN সেট করতে পারেন বা মুছে ফেলতে পারেন।'
      : 'বর্তমানে কোনো PIN সেট করা নেই। অন্য ডিভাইস থেকে নিরাপদ রাখতে চাইলে PIN দিতে পারেন।';
    els.clearPinBtn.classList.toggle('hidden', !pinOn);
  }

  async function reloadProviders() {
    const resp = await apiFetch('/api/providers');
    state.providers = await resp.json();
    updateProviderSummary();
    renderModelPicker();
    renderProvidersSettings();
  }

  function openSettings(tabName = 'providers') {
    els.settingsModal.classList.remove('hidden');
    els.settingsModal.setAttribute('aria-hidden', 'false');
    document.querySelectorAll('.tab-btn').forEach((b) => {
      b.classList.toggle('active', b.dataset.tab === tabName);
    });
    document.getElementById('tabProviders').classList.toggle('active', tabName === 'providers');
    document.getElementById('tabPreferences').classList.toggle('active', tabName === 'preferences');
    document.getElementById('tabMobile').classList.toggle('active', tabName === 'mobile');
  }

  function closeSettings() {
    els.settingsModal.classList.add('hidden');
    els.settingsModal.setAttribute('aria-hidden', 'true');
  }

  function closeMobileSidebar() {
    els.sidebar.classList.remove('open');
    els.sidebarBackdrop.classList.add('hidden');
  }

  async function bootstrap() {
    const authRes = await fetch('/api/auth/status').then((r) => r.json());
    if (authRes.pin_enabled && !state.pin) {
      showPinOverlay();
      return;
    }

    try {
      const resp = await apiFetch('/api/bootstrap');
      const data = await resp.json();
      state.settings = data.settings || {};
      state.providers = data.providers || [];
      state.conversations = data.conversations || [];
      state.network = data.network || { port: 8000, lan_ips: [] };
      state.mode = state.settings.default_mode || 'auto';

      applyTheme(state.settings.theme || 'dark');
      updateProviderSummary();
      renderModelPicker();
      renderConversations();
      renderProvidersSettings();
      renderPreferencesTab();
      renderMobileTab();
      renderMessages();

      if (window.FreeAIHubVoice && typeof window.FreeAIHubVoice.syncSettings === 'function') {
        window.FreeAIHubVoice.syncSettings(state.settings);
      }
    } catch (err) {
      if (err.message !== 'PIN required') {
        toast(`লোড করতে সমস্যা হয়েছে: ${err.message}`, 'error');
      }
    }
  }

  function bindEvents() {
    els.pinForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const pinVal = els.pinInput.value.trim();
      const resp = await fetch('/api/auth/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin: pinVal }),
      });
      if (!resp.ok) {
        els.pinError.textContent = 'ভুল PIN! আবার চেষ্টা করুন।';
        els.pinError.classList.remove('hidden');
        return;
      }
      state.pin = pinVal;
      sessionStorage.setItem('freeai_pin', pinVal);
      els.pinInput.value = '';
      hidePinOverlay();
      await bootstrap();
    });

    els.sidebarToggleBtn.addEventListener('click', () => {
      if (window.innerWidth <= 768) {
        els.sidebar.classList.toggle('open');
        els.sidebarBackdrop.classList.toggle('hidden', !els.sidebar.classList.contains('open'));
      } else {
        els.sidebar.classList.toggle('collapsed');
      }
    });
    els.sidebarCloseBtn.addEventListener('click', closeMobileSidebar);
    els.sidebarBackdrop.addEventListener('click', closeMobileSidebar);

    els.newChatBtn.addEventListener('click', () => {
      startNewChat();
      closeMobileSidebar();
    });

    let searchTimer = null;
    els.convSearchInput.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        refreshConversations(els.convSearchInput.value.trim());
      }, 220);
    });

    els.modelPickerBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      els.modelPickerMenu.classList.toggle('hidden');
    });
    document.addEventListener('click', (e) => {
      if (!els.modelPickerMenu.contains(e.target) && e.target !== els.modelPickerBtn) {
        els.modelPickerMenu.classList.add('hidden');
      }
    });

    els.compareToggleBtn.addEventListener('click', () => {
      state.compareMode = !state.compareMode;
      els.compareToggleBtn.classList.toggle('active', state.compareMode);
      els.compareBar.classList.toggle('hidden', !state.compareMode);
    });

    els.themeToggleBtn.addEventListener('click', async () => {
      const next = state.settings.theme === 'light' ? 'dark' : 'light';
      applyTheme(next);
      await apiFetch('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: next }),
      });
    });

    els.openSettingsBtn.addEventListener('click', () => {
      closeMobileSidebar();
      openSettings('providers');
    });
    els.topSettingsBtn.addEventListener('click', () => openSettings('providers'));
    els.onboardingConnectBtn.addEventListener('click', () => openSettings('providers'));
    els.openMobileBtn.addEventListener('click', () => {
      closeMobileSidebar();
      openSettings('mobile');
    });
    els.closeSettingsBtn.addEventListener('click', closeSettings);
    els.settingsModal.addEventListener('click', (e) => {
      if (e.target === els.settingsModal) closeSettings();
    });

    document.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => openSettings(btn.dataset.tab));
    });

    els.savePrefsBtn.addEventListener('click', async () => {
      const payload = {
        system_prompt: els.prefSystemPrompt.value,
        default_mode: els.prefDefaultMode.value,
        auto_fallback: els.prefAutoFallback.value,
        voice_engine: els.prefVoiceEngine.value,
        voice_language: els.prefVoiceLang.value,
        voice_gender: els.prefVoiceGender.value,
      };
      const resp = await apiFetch('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      state.settings = await resp.json();
      renderModelPicker();
      if (window.FreeAIHubVoice) window.FreeAIHubVoice.syncSettings(state.settings);
      toast('সেটিংস সেভ হয়েছে!', 'success');
    });

    els.savePinBtn.addEventListener('click', async () => {
      const pinVal = els.prefNewPin.value.trim();
      if (!pinVal) {
        toast('PIN লিখুন।', 'error');
        return;
      }
      const resp = await apiFetch('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_pin: pinVal }),
      });
      state.settings = await resp.json();
      state.pin = pinVal;
      sessionStorage.setItem('freeai_pin', pinVal);
      els.prefNewPin.value = '';
      renderPreferencesTab();
      toast('মোবাইল PIN সেট করা হয়েছে!', 'success');
    });

    els.clearPinBtn.addEventListener('click', async () => {
      const resp = await apiFetch('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_pin: '' }),
      });
      state.settings = await resp.json();
      state.pin = '';
      sessionStorage.removeItem('freeai_pin');
      renderPreferencesTab();
      toast('PIN লক বন্ধ করা হয়েছে।', 'info');
    });

    els.attachBtn.addEventListener('click', () => els.fileInput.click());
    els.fileInput.addEventListener('change', () => {
      handleFilesSelected(els.fileInput.files);
      els.fileInput.value = '';
    });

    els.promptInput.addEventListener('input', () => {
      els.promptInput.style.height = 'auto';
      els.promptInput.style.height = `${Math.min(180, els.promptInput.scrollHeight)}px`;
    });

    els.promptInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    els.promptInput.addEventListener('paste', (e) => {
      const files = e.clipboardData?.files;
      if (files && files.length) {
        handleFilesSelected(files);
      }
    });

    els.sendBtn.addEventListener('click', () => sendMessage());

    document.querySelectorAll('.starter-card').forEach((card) => {
      card.addEventListener('click', () => {
        if (card.dataset.action === 'voice') {
          window.FreeAIHubVoice?.open();
        } else if (card.dataset.prompt) {
          sendMessage({ overridePrompt: card.dataset.prompt });
        }
      });
    });

    const openVoice = () => window.FreeAIHubVoice?.open();
    els.openVoiceBtn.addEventListener('click', openVoice);
    els.composerVoiceBtn.addEventListener('click', openVoice);
  }

  window.FreeAIHub = {
    getContext: () => ({
      pin: state.pin,
      mode: state.mode,
      provider_id: state.selectedProvider,
      model_id: state.selectedModel,
      conversation_id: state.activeConvId,
      settings: state.settings,
    }),
    onVoiceTurnSaved: async () => {
      if (state.activeConvId) {
        await loadConversation(state.activeConvId);
        await refreshConversations(els.convSearchInput.value.trim());
      }
    },
    toast,
  };

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    bootstrap();
  });
})();
