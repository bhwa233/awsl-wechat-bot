const $ = id => document.getElementById(id);
const POLL_INTERVAL_MS = 3000;
let selectedPath = '';
let selectedName = '';
let openingChat = false;
let refreshingState = false;
let sendingText = false;
let pollingState = false;
let pollTimer = null;

function showChatPanel(show) {
    document.body.classList.toggle('chat-open', show);
}

function getToken() {
    return localStorage.getItem('awsl_api_token') || '';
}

function setStatus(text, error = false, loading = false) {
    const el = $('status');
    el.textContent = text;
    el.classList.toggle('error', error);
    el.classList.toggle('loading', loading);
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function avatarText(name) {
    const text = String(name || '').trim();
    if (!text) return '?';
    const first = Array.from(text)[0] || '?';
    return first.toUpperCase();
}

async function api(path, body = {}) {
    const headers = { 'Content-Type': 'application/json' };
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;

    const res = await fetch(path, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
    });
    const text = await res.text();
    let data = {};
    try {
        data = text ? JSON.parse(text) : {};
    } catch {
        const detail = text.trim().startsWith('<') ? res.statusText : text;
        data = { detail: detail || `HTTP ${res.status}` };
    }
    return { ok: res.ok, status: res.status, data };
}

function renderChatList(chatList) {
    const items = chatList?.items || [];
    $('chatCount').textContent = String(items.length);
    if (!items.length) {
        $('chatList').innerHTML = '<div class="empty">未解析到聊天列表</div>';
        return;
    }
    $('chatList').innerHTML = items.map(item => {
        const unread = item.unread ? `<span class="badge">${escapeHtml(item.unread)}</span>` : '';
        const isActive = selectedName
            ? item.name === selectedName
            : item.path && item.path === selectedPath;
        if (isActive) {
            if (item.name) selectedName = item.name;
            if (item.path) selectedPath = item.path;
        }
        return `<button class="chat-row${isActive ? ' active' : ''}" data-path="${escapeHtml(item.path || '')}" data-name="${escapeHtml(item.name || '')}" type="button">
            <span class="chat-avatar">${escapeHtml(avatarText(item.name))}</span>
            <span class="chat-copy">
                <span class="chat-head">
                    <span class="chat-name">${escapeHtml(item.name)}${unread}</span>
                    <span class="chat-time">${escapeHtml(item.time || '')}</span>
                </span>
                <span class="chat-meta">${escapeHtml(item.preview || '')}</span>
            </span>
        </button>`;
    }).join('');
    document.querySelectorAll('.chat-row[data-path]').forEach(row => {
        row.addEventListener('click', () => openChat(row.dataset.path, row.dataset.name || ''));
    });
}

function messageAlign(msg) {
    if (msg.align === 'left' || msg.align === 'right' || msg.align === 'center') return msg.align;
    if (msg.is_self === true) return 'right';
    if (msg.kind === 'timestamp' || msg.kind === 'system') return 'center';
    return 'left';
}

function renderMessageBubble(msg) {
    const content = escapeHtml(msg.content || '');
    if (!msg.quote) return `<div class="message-bubble">${content}</div>`;
    const text = content ? `<div class="message-text">${content}</div>` : '';
    const sender = escapeHtml(msg.quote.sender || '');
    const quoteContent = escapeHtml(msg.quote.content || '');
    return `<div class="message-bubble">${text}<div class="message-quote"><div class="quote-sender">${sender}</div><div class="quote-content">${quoteContent}</div></div></div>`;
}

function renderChatArea(chatArea, chatList, options = {}) {
    const chatAreaEl = $('chatArea');
    const wasNearBottom = chatAreaEl.scrollHeight - chatAreaEl.scrollTop - chatAreaEl.clientHeight < 80;
    const oldScrollTop = chatAreaEl.scrollTop;
    const messages = chatArea?.messages || [];
    $('messageCount').textContent = String(messages.length);
    $('chatTitle').textContent = chatArea?.title || '聊天区域';
    if (!chatArea?.available) {
        const hasChats = (chatList?.items || []).length > 0;
        const text = hasChats ? '请选择聊天列表中的会话' : '主窗口当前没有可解析的聊天消息区';
        chatAreaEl.innerHTML = `<div class="empty">${text}</div>`;
        return;
    }
    if (!messages.length) {
        chatAreaEl.innerHTML = '<div class="empty">消息区为空</div>';
        return;
    }
    chatAreaEl.innerHTML = messages.map(msg => {
        const align = messageAlign(msg);
        return `<div class="message-line ${align}">
            ${renderMessageBubble(msg)}
        </div>`;
    }).join('');
    if (options.preserveScroll && !wasNearBottom) {
        chatAreaEl.scrollTop = oldScrollTop;
        return;
    }
    chatAreaEl.scrollTop = chatAreaEl.scrollHeight;
}

function renderState(data) {
    renderChatList(data.chat_list);
    renderChatArea(data.chat_area, data.chat_list);
}

function renderCurrentChat(data, options = {}) {
    renderChatArea(data.chat_area, data.chat_list, options);
}

function renderError(data) {
    $('chatCount').textContent = '0';
    $('messageCount').textContent = '0';
    $('chatList').innerHTML = '<div class="empty">未解析到聊天列表</div>';
    $('chatArea').innerHTML = `<div class="empty error">${escapeHtml(data?.detail || data?.message || '请求失败')}</div>`;
}

function clearChatArea(text = '正在切换聊天') {
    $('messageCount').textContent = '0';
    $('chatArea').innerHTML = `<div class="loading-card" aria-label="${escapeHtml(text)}">
        <div class="loading-row"><div class="loading-bubble"></div></div>
        <div class="loading-row"><div class="loading-bubble"></div></div>
        <div class="loading-row"><div class="loading-bubble"></div></div>
    </div>`;
}

function setChatListBusy(busy) {
    document.querySelectorAll('.chat-row').forEach(row => {
        row.disabled = busy;
    });
}

function setButtonBusy(button, busy) {
    button.disabled = busy;
    button.classList.toggle('loading', busy);
    button.setAttribute('aria-busy', busy ? 'true' : 'false');
}

function showTokenModal(show) {
    $('tokenModal').classList.toggle('show', show);
    $('tokenModal').setAttribute('aria-hidden', show ? 'false' : 'true');
    if (show) {
        $('token').value = getToken();
        $('token').focus();
    }
}

async function refreshState() {
    if (refreshingState) return;
    refreshingState = true;
    const btn = $('refresh');
    setButtonBusy(btn, true);
    setStatus('正在读取主窗口 UIA 状态', false, true);
    try {
        const res = await api('/api/console/state', {});
        if (!res.ok) {
            setStatus(res.data?.detail || res.data?.message || `请求失败 ${res.status}`, true);
            renderError(res.data);
            return;
        }
        renderState(res.data);
        setStatus(res.data.available ? '主窗口状态已更新' : (res.data.message || '主窗口不可用'), !res.data.available);
    } catch (err) {
        setStatus('网络错误，无法读取 console 状态', true);
    } finally {
        refreshingState = false;
        setButtonBusy(btn, false);
    }
}

async function openChat(path, name = '') {
    if (!path || openingChat) return;
    openingChat = true;
    setChatListBusy(true);
    selectedPath = path;
    selectedName = name || selectedName;
    $('chatTitle').textContent = selectedName || '聊天区域';
    clearChatArea();
    showChatPanel(true);
    setStatus('正在切换聊天', false, true);
    try {
        const res = await api('/api/console/open-chat', { path });
        if (!res.ok || !res.data?.available) {
            setStatus(res.data?.detail || res.data?.message || '切换聊天失败', true);
            renderError(res.data);
            return;
        }
        renderState(res.data);
        setStatus('聊天已切换，输入框已聚焦');
    } finally {
        openingChat = false;
        setChatListBusy(false);
    }
}

async function sendText() {
    if (sendingText) return;
    const input = $('messageInput');
    const text = input.value.trim();
    if (!text) return;
    sendingText = true;
    setButtonBusy($('sendText'), true);
    setStatus('正在发送消息', false, true);
    try {
        const res = await api('/api/console/send-text', { text });
        if (!res.ok || !res.data?.available) {
            setStatus(res.data?.detail || res.data?.message || '发送失败', true);
            renderError(res.data);
            return;
        }
        input.value = '';
        renderCurrentChat(res.data);
        setStatus('消息已发送');
    } finally {
        sendingText = false;
        setButtonBusy($('sendText'), false);
    }
}

function shouldPoll() {
    return document.visibilityState === 'visible'
        && !openingChat
        && !refreshingState
        && !sendingText;
}

async function pollState() {
    if (pollingState || !shouldPoll()) return;
    pollingState = true;
    try {
        const res = await api('/api/console/state', {});
        if (!res.ok || !res.data?.available) return;
        if (selectedPath || selectedName || document.body.classList.contains('chat-open')) {
            renderChatList(res.data.chat_list);
            renderCurrentChat(res.data, { preserveScroll: true });
            return;
        }
        renderState(res.data);
    } catch {
        // 轮询失败不打断当前操作，也不覆盖手动刷新/发送的状态提示。
    } finally {
        pollingState = false;
    }
}

function startPolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(pollState, POLL_INTERVAL_MS);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') pollState();
    });
}

function init() {
    $('tokenBtn').addEventListener('click', () => showTokenModal(true));
    $('backToList').addEventListener('click', () => showChatPanel(false));
    $('closeToken').addEventListener('click', () => showTokenModal(false));
    $('tokenModal').addEventListener('click', e => {
        if (e.target === $('tokenModal')) showTokenModal(false);
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') showTokenModal(false);
    });
    $('saveToken').addEventListener('click', () => {
        localStorage.setItem('awsl_api_token', $('token').value.trim());
        setStatus('Token 已保存');
        showTokenModal(false);
    });
    $('refresh').addEventListener('click', refreshState);
    $('sendText').addEventListener('click', sendText);
    $('messageInput').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendText();
        }
    });
    refreshState();
    startPolling();
}

init();
