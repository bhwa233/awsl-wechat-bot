const $ = id => document.getElementById(id);

function getToken() {
    return localStorage.getItem('awsl_api_token') || '';
}

function setStatus(text, error = false) {
    const el = $('status');
    el.textContent = text;
    el.classList.toggle('error', error);
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
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
        data = { detail: text || res.statusText };
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
        return `<div class="chat-row">
            <div class="chat-name">${escapeHtml(item.name)}${unread}</div>
            <div class="chat-meta">${escapeHtml(item.preview || '')}${escapeHtml(muted)}</div>
        </div>`;
    }).join('');
}

function renderChatArea(chatArea) {
    const messages = chatArea?.messages || [];
    $('messageCount').textContent = String(messages.length);
    if (!chatArea?.available) {
        $('chatArea').innerHTML = '<div class="empty">主窗口当前没有可解析的聊天消息区</div>';
        return;
    }
    if (!messages.length) {
        $('chatArea').innerHTML = '<div class="empty">消息区为空</div>';
        return;
    }
    $('chatArea').innerHTML = messages.map(msg => {
        const cls = msg.kind === 'timestamp' ? 'message timestamp' : 'message';
        return `<div class="${cls}">
            <div>${escapeHtml(msg.content || '')}</div>
        </div>`;
    }).join('');
}

function renderState(data) {
    renderChatList(data.chat_list);
    renderChatArea(data.chat_area);
}

function renderError(data) {
    $('chatCount').textContent = '0';
    $('messageCount').textContent = '0';
    $('chatList').innerHTML = '<div class="empty">未解析到聊天列表</div>';
    $('chatArea').innerHTML = `<div class="empty error">${escapeHtml(data?.detail || data?.message || '请求失败')}</div>`;
}

async function refreshState() {
    const btn = $('refresh');
    btn.disabled = true;
    setStatus('正在读取主窗口 UIA 状态');
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
        btn.disabled = false;
    }
}

function init() {
    $('token').value = getToken();
    $('saveToken').addEventListener('click', () => {
        localStorage.setItem('awsl_api_token', $('token').value.trim());
        setStatus('Token 已保存');
    });
    $('refresh').addEventListener('click', refreshState);
    refreshState();
}

init();
