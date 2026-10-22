const $ = id => document.getElementById(id);
const POLL_INTERVAL_MS = 3000;
let selectedPath = '';
let selectedName = '';
let openingChat = false;
let refreshingState = false;
let scrollingChats = false;
let sendingMessage = false;
let pollingState = false;
let pollTimer = null;
let maxBinaryFileBytes = 0;
let attachment = { message: '', filename: '', preview: '' };

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
    if (!selectedName && data.chat_area?.available) {
        const current = (data.chat_list?.items || []).find(item => item.name === data.chat_area.title);
        if (current) {
            selectedName = current.name;
            selectedPath = current.path || '';
            showChatPanel(true);
        }
    }
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

function getMessageType() {
    return $('messageType')?.value || 'text';
}

function syncComposerType(reset = true) {
    const type = getMessageType();
    const isText = type === 'text' || type === 'voice';
    const isAttachment = type === 'image' || type === 'file' || type === 'voice_file';
    $('messageInput').hidden = !isText;
    $('fileDropzone').hidden = !isAttachment;
    $('fileInput').accept = type === 'image' ? 'image/*' : type === 'voice_file' ? 'audio/mpeg,.mp3' : '';
    if (reset && isAttachment) resetAttachment();
    if (type === 'voice_file' && !attachment.message) {
        $('fileInfo').textContent = '选择 MP3 文件，最长 60 秒';
    }
}

function resetAttachment() {
    attachment = { message: '', filename: '', preview: '' };
    $('fileInput').value = '';
    $('fileDropzone').classList.remove('has-file', 'dragover');
    $('filePreview').hidden = true;
    $('filePreview').removeAttribute('src');
    $('fileInfo').textContent = '点击选择 / 拖拽 / Ctrl+V 粘贴';
}

function formatFileSize(bytes) {
    if (!bytes || bytes < 0) return '';
    const units = ['B', 'KB', 'MB', 'GB'];
    let idx = 0;
    let value = bytes;
    while (value >= 1024 && idx < units.length - 1) {
        value /= 1024;
        idx++;
    }
    return (idx === 0 ? value : value.toFixed(1)) + ' ' + units[idx];
}

function loadAttachmentFile(file) {
    if (!file) return;
    if (maxBinaryFileBytes > 0 && file.size > maxBinaryFileBytes) {
        setStatus(`文件不能超过 ${formatFileSize(maxBinaryFileBytes)}`, true);
        resetAttachment();
        return;
    }
    const type = getMessageType();
    if (type === 'image' && !file.type.startsWith('image/')) {
        setStatus('请选择图片文件', true);
        return;
    }
    if (type === 'voice_file' && !file.name.toLowerCase().endsWith('.mp3')) {
        setStatus('语音文件仅支持 MP3', true);
        return;
    }
    const reader = new FileReader();
    reader.onload = event => {
        const dataUrl = String(event.target.result || '');
        attachment = {
            message: dataUrl.split(',')[1] || '',
            filename: file.name || '',
            preview: dataUrl,
        };
        $('fileDropzone').classList.add('has-file');
        if (file.type.startsWith('image/')) {
            $('filePreview').src = dataUrl;
            $('filePreview').hidden = false;
        } else {
            $('filePreview').hidden = true;
            $('filePreview').removeAttribute('src');
        }
        const size = formatFileSize(file.size);
        $('fileInfo').textContent = `${file.name}${size ? ` (${size})` : ''}`;
    };
    reader.readAsDataURL(file);
}

async function loadCapabilities() {
    try {
        const res = await fetch('/api/health');
        const data = await res.json();
        maxBinaryFileBytes = Number(data?.capabilities?.max_binary_file_bytes) || 0;
    } catch {
        maxBinaryFileBytes = 0;
    }
}

function buildMessagePayload() {
    const messageType = getMessageType();
    if (messageType === 'voice_file' && attachment.message) {
        return { message_type: 'voice_file', message: attachment.message, filename: attachment.filename };
    }
    if (messageType === 'text' || messageType === 'voice') {
        const message = $('messageInput').value.trim();
        if (!message) return null;
        return { message_type: messageType, message, filename: '' };
    }
    if (!attachment.message) {
        setStatus(messageType === 'image' ? '请选择图片' : '请选择文件', true);
        return null;
    }
    return {
        message_type: messageType,
        message: attachment.message,
        filename: messageType === 'file' ? attachment.filename : '',
    };
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
    if (refreshingState || scrollingChats) return;
    refreshingState = true;
    const btn = $('refresh');
    setButtonBusy(btn, true);
    setStatus('正在读取主窗口状态', false, true);
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

async function scrollChats(direction) {
    if (scrollingChats || openingChat || refreshingState || sendingMessage) return;
    scrollingChats = true;
    setChatListBusy(true);
    setButtonBusy($('scrollChatsUp'), true);
    setButtonBusy($('scrollChatsDown'), true);
    setStatus(direction === 'up' ? '正在向上滚动聊天列表' : '正在向下滚动聊天列表', false, true);
    try {
        const res = await api('/api/console/scroll-chats', { direction });
        if (!res.ok || !res.data?.available) {
            setStatus(res.data?.detail || res.data?.message || '滚动聊天列表失败', true);
            return;
        }
        renderState(res.data);
        setStatus(direction === 'up' ? '聊天列表已向上滚动' : '聊天列表已向下滚动');
    } catch {
        setStatus('网络错误，无法滚动聊天列表', true);
    } finally {
        scrollingChats = false;
        setChatListBusy(false);
        setButtonBusy($('scrollChatsUp'), false);
        setButtonBusy($('scrollChatsDown'), false);
    }
}

async function openChat(path, name = '') {
    if (!path || openingChat || scrollingChats || sendingMessage) return;
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

async function sendMessage() {
    if (sendingMessage || scrollingChats) return;
    const payload = buildMessagePayload();
    if (!payload) return;
    if (!selectedName) {
        setStatus('请先选择聊天', true);
        return;
    }
    payload.chat_name = selectedName;
    sendingMessage = true;
    setButtonBusy($('sendMessage'), true);
    setStatus('正在发送消息', false, true);
    try {
        const res = await api('/api/console/send-message', payload);
        if (!res.ok || !res.data?.available) {
            setStatus(res.data?.detail || res.data?.message || '发送失败', true);
            renderError(res.data);
            return;
        }
        if (payload.message_type === 'text' || payload.message_type === 'voice') $('messageInput').value = '';
        else resetAttachment();
        renderCurrentChat(res.data);
        setStatus('消息已发送');
    } finally {
        sendingMessage = false;
        setButtonBusy($('sendMessage'), false);
    }
}

function shouldPoll() {
    return document.visibilityState === 'visible'
        && !openingChat
        && !refreshingState
        && !scrollingChats
        && !sendingMessage;
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
    $('scrollChatsUp').addEventListener('click', () => scrollChats('up'));
    $('scrollChatsDown').addEventListener('click', () => scrollChats('down'));
    $('messageType').addEventListener('change', () => syncComposerType());
    $('fileInput').addEventListener('change', e => loadAttachmentFile(e.target.files[0]));
    $('fileDropzone').addEventListener('dragover', e => {
        e.preventDefault();
        $('fileDropzone').classList.add('dragover');
    });
    $('fileDropzone').addEventListener('dragleave', () => $('fileDropzone').classList.remove('dragover'));
    $('fileDropzone').addEventListener('drop', e => {
        e.preventDefault();
        $('fileDropzone').classList.remove('dragover');
        loadAttachmentFile(e.dataTransfer.files[0]);
    });
    document.addEventListener('paste', e => {
        const type = getMessageType();
        if (type !== 'image' && type !== 'file') return;
        const items = e.clipboardData?.items;
        if (!items) return;
        for (const item of items) {
            if (item.kind !== 'file') continue;
            if (type === 'image' && !item.type.startsWith('image/')) continue;
            const file = item.getAsFile();
            if (!file) continue;
            e.preventDefault();
            if (type === 'image') {
                const ext = item.type.split('/')[1] || 'png';
                loadAttachmentFile(new File([file], `pasted-${Date.now()}.${ext}`, { type: item.type }));
                return;
            }
            loadAttachmentFile(file);
            return;
        }
    });
    $('sendMessage').addEventListener('click', sendMessage);
    $('messageInput').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    syncComposerType(false);
    loadCapabilities();
    refreshState();
    startPolling();
}

init();
