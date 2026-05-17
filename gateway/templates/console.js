const $ = id => document.getElementById(id);
let selectedPath = '';
let selectedName = '';
let openingChat = false;

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
        const muted = item.muted ? ' · 免打扰' : '';
        const active = item.path && item.path === selectedPath ? ' active' : '';
        if (active && item.name) selectedName = item.name;
        return `<button class="chat-row${active}" data-path="${escapeHtml(item.path || '')}" type="button">
            <span class="chat-avatar">${escapeHtml(avatarText(item.name))}</span>
            <span class="chat-copy">
                <span class="chat-name">${escapeHtml(item.name)}${unread}</span>
                <span class="chat-meta">${escapeHtml(item.preview || '')}${escapeHtml(muted)}</span>
            </span>
        </button>`;
    }).join('');
    document.querySelectorAll('.chat-row[data-path]').forEach(row => {
        row.addEventListener('click', () => openChat(row.dataset.path, row));
    });
}

function messageAlign(msg) {
    if (msg.align === 'left' || msg.align === 'right' || msg.align === 'center') return msg.align;
    if (msg.is_self === true) return 'right';
    if (msg.kind === 'timestamp' || msg.kind === 'system') return 'center';
    return 'left';
}

function renderChatArea(chatArea, chatList) {
    const messages = chatArea?.messages || [];
    $('messageCount').textContent = String(messages.length);
    $('chatTitle').textContent = selectedName || chatArea?.title || '聊天区域';
    if (!chatArea?.available) {
        const hasChats = (chatList?.items || []).length > 0;
        const text = hasChats ? '请选择聊天列表中的会话' : '主窗口当前没有可解析的聊天消息区';
        $('chatArea').innerHTML = `<div class="empty">${text}</div>`;
        return;
    }
    if (!messages.length) {
        $('chatArea').innerHTML = '<div class="empty">消息区为空</div>';
        return;
    }
    $('chatArea').innerHTML = messages.map(msg => {
        const align = messageAlign(msg);
        return `<div class="message-line ${align}">
            <div class="message-bubble">${escapeHtml(msg.content || '')}</div>
        </div>`;
    }).join('');
    $('chatArea').scrollTop = $('chatArea').scrollHeight;
}

function renderState(data) {
    renderChatList(data.chat_list);
    renderChatArea(data.chat_area, data.chat_list);
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
        setButtonBusy(btn, false);
    }
}

async function openChat(path, row = null) {
    if (!path || openingChat) return;
    openingChat = true;
    setChatListBusy(true);
    selectedPath = path;
    selectedName = row?.querySelector('.chat-name')?.childNodes?.[0]?.textContent || selectedName;
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
    const input = $('messageInput');
    const text = input.value.trim();
    if (!text) return;
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
        renderState(res.data);
        setStatus('消息已发送');
    } finally {
        setButtonBusy($('sendText'), false);
    }
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
}

init();
