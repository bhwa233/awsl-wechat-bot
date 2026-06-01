// 控制台 Tab: 布局/交互照搬 gateway console, 聊天气泡复用 chat.js 风格 class。
// 数据走 /api/console/* (Cortex 代理 Gateway), 仅 Windows Gateway 可用。

const CONSOLE_POLL_INTERVAL_MS = 3000;

window.consoleTab = (() => {
    let selectedPath = '';
    let selectedName = '';
    let opening = false;
    let refreshing = false;
    let sending = false;
    let polling = false;
    let pollTimer = null;
    let activated = false;
    let unsupported = false;

    function setStatus(text, error = false) {
        const el = $('consoleStatus');
        if (!el) return;
        el.textContent = text;
        el.classList.toggle('error', error);
    }

    function avatarText(name) {
        const text = String(name || '').trim();
        if (!text) return '?';
        return (Array.from(text)[0] || '?').toUpperCase();
    }

    // Cortex 全局 api(method, path, {body}) 适配 console 的 POST 接口
    async function consoleApi(path, body = {}) {
        const r = await api('POST', path, { body });
        return r;
    }

    function renderList(chatList) {
        const items = chatList?.items || [];
        $('consoleChatCount').textContent = String(items.length);
        const box = $('consoleChatList');
        if (!items.length) {
            box.innerHTML = '<div class="empty-hint">未解析到聊天列表</div>';
            return;
        }
        box.innerHTML = items.map(item => {
            const unread = item.unread ? `<span class="console-badge">${escapeHtml(item.unread)}</span>` : '';
            const isActive = selectedName
                ? item.name === selectedName
                : item.path && item.path === selectedPath;
            if (isActive) {
                if (item.name) selectedName = item.name;
                if (item.path) selectedPath = item.path;
            }
            return `<button class="console-row${isActive ? ' active' : ''}" data-path="${escapeHtml(item.path || '')}" data-name="${escapeHtml(item.name || '')}" type="button">
                <span class="console-avatar">${escapeHtml(avatarText(item.name))}</span>
                <span class="console-copy">
                    <span class="console-row-head">
                        <span class="console-name">${escapeHtml(item.name)}${unread}</span>
                        <span class="console-time">${escapeHtml(item.time || '')}</span>
                    </span>
                    <span class="console-preview">${escapeHtml(item.preview || '')}</span>
                </span>
            </button>`;
        }).join('');
        box.querySelectorAll('.console-row[data-path]').forEach(row => {
            row.addEventListener('click', () => openChat(row.dataset.path, row.dataset.name || ''));
        });
    }

    function msgAlign(msg) {
        if (msg.align === 'left' || msg.align === 'right' || msg.align === 'center') return msg.align;
        if (msg.is_self === true) return 'right';
        if (msg.kind === 'timestamp' || msg.kind === 'system') return 'center';
        return 'left';
    }

    function renderBubbleInner(msg) {
        const content = escapeHtml(msg.content || '');
        if (!msg.quote) return content;
        const sender = escapeHtml(msg.quote.sender || '');
        const qc = escapeHtml(msg.quote.content || '');
        const text = content ? `<div class="msg-text">${content}</div>` : '';
        return `${text}<div class="msg-quote">${sender ? `<div class="msg-quote-name">${sender}</div>` : ''}${qc}</div>`;
    }

    function renderMessages(chatArea, chatList, options = {}) {
        const box = $('consoleMessages');
        const wasNearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
        const oldScrollTop = box.scrollTop;
        const messages = chatArea?.messages || [];
        $('consoleChatTitle').textContent = chatArea?.title || '聊天区域';
        if (!chatArea?.available) {
            const hasChats = (chatList?.items || []).length > 0;
            const text = hasChats ? '请选择聊天列表中的会话' : '主窗口当前没有可解析的聊天消息区';
            box.innerHTML = `<div class="chat-empty"><div class="chat-empty-icon">🖥️</div><p>${escapeHtml(text)}</p></div>`;
            return;
        }
        if (!messages.length) {
            box.innerHTML = '<div class="empty-hint">消息区为空</div>';
            return;
        }
        box.innerHTML = messages.map(msg => {
            const align = msgAlign(msg);
            if (align === 'center') {
                return `<div class="msg system"><div class="msg-system">${escapeHtml(msg.content || '')}</div></div>`;
            }
            if (align === 'right') {
                return `<div class="msg self"><div class="msg-bubble self">${renderBubbleInner(msg)}</div></div>`;
            }
            const name = msg.sender || msg.name || '';
            const senderHtml = name ? `<div class="msg-sender">${escapeHtml(name)}</div>` : '';
            return `<div class="msg other"><div class="msg-bubble other">${senderHtml}${renderBubbleInner(msg)}</div></div>`;
        }).join('');
        if (options.preserveScroll && !wasNearBottom) {
            box.scrollTop = oldScrollTop;
            return;
        }
        box.scrollTop = box.scrollHeight;
    }

    function renderState(data, options = {}) {
        renderList(data.chat_list);
        renderMessages(data.chat_area, data.chat_list, options);
    }

    function renderError(detail) {
        $('consoleChatCount').textContent = '0';
        $('consoleChatList').innerHTML = '<div class="empty-hint">未解析到聊天列表</div>';
        $('consoleMessages').innerHTML = `<div class="empty-hint">${escapeHtml(detail || '请求失败')}</div>`;
    }

    function showUnsupported(detail) {
        unsupported = true;
        $('consoleChatCount').textContent = '0';
        $('consoleChatList').innerHTML = '<div class="empty-hint">仅支持 Windows</div>';
        $('consoleMessages').innerHTML = `<div class="chat-empty"><div class="chat-empty-icon">🪟</div><p>${escapeHtml(detail || 'Console 仅支持 Windows Gateway')}</p></div>`;
        setStatus('当前 Gateway 平台不支持 Console', true);
    }

    function clearChatArea() {
        $('consoleMessages').innerHTML = '<div class="loading-hint">正在切换聊天...</div>';
    }

    function setListBusy(busy) {
        $('consoleChatList').querySelectorAll('.console-row').forEach(row => { row.disabled = busy; });
    }

    async function refresh() {
        if (refreshing || unsupported) return;
        refreshing = true;
        const btn = $('consoleRefreshBtn');
        if (btn) btn.disabled = true;
        setStatus('正在读取主窗口 UIA 状态');
        const box = $('consoleMessages');
        if (box && !$('consoleChatList')?.children.length) {
            box.innerHTML = '<div class="loading-hint">正在读取主窗口状态...</div>';
        }
        try {
            const r = await consoleApi('/api/console/state', {});
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok) { setStatus(r.data?.detail || r.error || `请求失败 ${r.status}`, true); renderError(r.data?.detail || r.error); return; }
            renderState(r.data);
            setStatus(r.data.available ? '主窗口状态已更新' : (r.data.message || '主窗口不可用'), !r.data.available);
        } catch {
            setStatus('网络错误，无法读取 console 状态', true);
        } finally {
            refreshing = false;
            if (btn) btn.disabled = false;
        }
    }

    async function openChat(path, name = '') {
        if (!path || opening || unsupported) return;
        opening = true;
        setListBusy(true);
        selectedPath = path;
        selectedName = name || selectedName;
        $('consoleChatTitle').textContent = selectedName || '聊天区域';
        clearChatArea();
        setStatus('正在切换聊天');
        try {
            const r = await consoleApi('/api/console/open-chat', { path });
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok || !r.data?.available) { setStatus(r.data?.detail || r.error || '切换聊天失败', true); renderError(r.data?.detail || r.error); return; }
            renderState(r.data);
            setStatus('聊天已切换，输入框已聚焦');
        } finally {
            opening = false;
            setListBusy(false);
        }
    }

    async function send() {
        if (sending || unsupported) return;
        const input = $('consoleMsgInput');
        const text = input.value.trim();
        if (!text) return;
        sending = true;
        const btn = $('consoleSendBtn');
        btn.disabled = true;
        setStatus('正在发送消息');
        try {
            const r = await consoleApi('/api/console/send-text', { text });
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok || !r.data?.available) { setStatus(r.data?.detail || r.error || '发送失败', true); renderError(r.data?.detail || r.error); return; }
            input.value = '';
            renderMessages(r.data.chat_area, r.data.chat_list);
            setStatus('消息已发送');
        } finally {
            sending = false;
            btn.disabled = false;
        }
    }

    function shouldPoll() {
        return activated
            && !unsupported
            && document.visibilityState === 'visible'
            && $('console-panel')?.classList.contains('active')
            && !opening && !refreshing && !sending;
    }

    async function poll() {
        if (polling || !shouldPoll()) return;
        polling = true;
        try {
            const r = await consoleApi('/api/console/state', {});
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok || !r.data?.available) return;
            if (selectedPath || selectedName) {
                renderList(r.data.chat_list);
                renderMessages(r.data.chat_area, r.data.chat_list, { preserveScroll: true });
                return;
            }
            renderState(r.data);
        } catch {
            // 轮询失败静默, 不覆盖手动操作状态
        } finally {
            polling = false;
        }
    }

    function startPolling() {
        if (pollTimer) return;
        pollTimer = window.setInterval(poll, CONSOLE_POLL_INTERVAL_MS);
    }

    function activate() {
        if (!activated) {
            activated = true;
            $('consoleMsgInput').addEventListener('keydown', e => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
            });
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'visible') poll();
            });
            startPolling();
            refresh();
            return;
        }
        if (!unsupported) refresh();
    }

    return { activate, refresh, send };
})();
