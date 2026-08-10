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
    let composer = null;
    let historyLoadGen = 0;

    function showChatPane(show) {
        $('console-panel')?.classList.toggle('console-chat-open', show);
    }

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

    async function ensureChatlogReady() {
        if (chatlogBackend && chatlogGroups.length) return true;
        try {
            const health = await api('GET', '/api/chatlog/health');
            if (health.ok && health.data) {
                chatlogBackend = health.data.backend || 'v1';
                if (health.data.media) {
                    chatlogMediaCaps = {
                        image: !!health.data.media.image,
                        sticker: !!health.data.media.sticker,
                        video_thumb: !!health.data.media.video_thumb,
                        voice: !!health.data.media.voice,
                        file: !!health.data.media.file,
                    };
                }
            }
            const groups = await api('GET', '/api/chatlog/groups');
            if (!groups.ok) return false;
            chatlogGroups = groups.data || [];
            buildGroupMapping();
            return true;
        } catch {
            return false;
        }
    }

    function chatlogDisplayName(group) {
        return group?.display_name || group?.remark || group?.nick_name || group?.username || '';
    }

    function findChatlogGroup(name) {
        const target = String(name || '').trim();
        if (!target) return null;
        return chatlogGroups.find(group => {
            const names = [group.username, group.display_name, group.remark, group.nick_name]
                .map(item => String(item || '').trim())
                .filter(Boolean);
            return names.includes(target);
        }) || null;
    }

    function renderChatlogMessages(msgs, options = {}) {
        const box = $('consoleMessages');
        const wasNearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
        const oldScrollTop = box.scrollTop;
        if (!msgs.length) {
            box.innerHTML = '<div class="empty-hint">暂无 Chatlog 消息</div>';
            return;
        }
        let lastDate = '';
        let html = '';
        for (const msg of msgs) {
            const date = msg.time.split('T')[0];
            if (date !== lastDate) {
                html += `<div class="msg-date">${date}</div>`;
                lastDate = date;
            }
            const time = formatTime(msg.time);
            if (isSystemLike(msg)) {
                html += `<div class="msg system">${formatMsgContent(msg)}</div>`;
                continue;
            }
            if (msg.is_self) {
                html += `<div class="msg self"><div class="msg-bubble self">${formatMsgContent(msg)}<div class="msg-time">${time}</div></div></div>`;
            } else {
                const name = msg.sender_name || msg.sender || '?';
                html += `<div class="msg other">${renderMessageAvatar(msg, name)}<div class="msg-bubble other"><div class="msg-sender">${escapeHtml(name)}</div>${formatMsgContent(msg)}<div class="msg-time">${time}</div></div></div>`;
            }
        }
        box.innerHTML = html;
        if (options.preserveScroll && !wasNearBottom) {
            box.scrollTop = oldScrollTop;
            return;
        }
        box.scrollTop = box.scrollHeight;
    }

    async function loadChatlogMessages(name, options = {}) {
        const gen = ++historyLoadGen;
        if (!options.quiet) $('consoleMessages').innerHTML = '<div class="loading-hint">正在加载 Chatlog 记录...</div>';
        if (!await ensureChatlogReady()) {
            if (gen === historyLoadGen) $('consoleMessages').innerHTML = '<div class="empty-hint">Chatlog 不可用</div>';
            return false;
        }
        const group = findChatlogGroup(name);
        if (!group) {
            if (gen === historyLoadGen) {
                $('consoleMessages').innerHTML = `<div class="empty-hint">未匹配到 Chatlog 会话: ${escapeHtml(name)}</div>`;
            }
            return false;
        }
        $('consoleChatTitle').textContent = chatlogDisplayName(group);
        await WECHAT_EMOJI_READY;
        const params = new URLSearchParams({
            group: group.username,
            limit: '101',
            offset: '0',
            desc: 'true',
        });
        const result = await api('GET', `/api/chatlog/messages?${params}`);
        if (gen !== historyLoadGen) return false;
        if (!result.ok) {
            $('consoleMessages').innerHTML = `<div class="empty-hint">${escapeHtml(result.data?.detail || result.error || 'Chatlog 加载失败')}</div>`;
            return false;
        }
        const msgs = result.data || [];
        if (msgs.length > 100) msgs.pop();
        msgs.reverse();
        renderChatlogMessages(msgs, options);
        Promise.allSettled([
            ensureChatlogSenderContacts(msgs),
            ensureChatlogMembers(group.username),
        ]).then(() => {
            if (gen !== historyLoadGen) return;
            renderChatlogMessages(msgs, { ...options, preserveScroll: true });
        });
        return true;
    }

    function renderState(data) {
        if (!selectedName && data.chat_area?.available) {
            const current = (data.chat_list?.items || []).find(item => item.name === data.chat_area.title);
            if (current) {
                selectedName = current.name;
                selectedPath = current.path || '';
                showChatPane(true);
            }
        }
        renderList(data.chat_list);
        if (!selectedName) {
            const hasChats = (data.chat_list?.items || []).length > 0;
            const text = hasChats ? '请选择聊天列表中的会话' : '主窗口当前没有可解析的聊天列表';
            $('consoleChatTitle').textContent = '聊天区域';
            $('consoleMessages').innerHTML = `<div class="chat-empty"><div class="chat-empty-icon">🖥️</div><p>${escapeHtml(text)}</p></div>`;
        }
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
            if (selectedName) await loadChatlogMessages(selectedName, { preserveScroll: true, quiet: true });
            setStatus(r.data.available ? '主窗口状态已更新，聊天记录来自 Chatlog' : (r.data.message || '主窗口不可用'), !r.data.available);
        } catch {
            setStatus('网络错误，无法读取 console 状态', true);
        } finally {
            refreshing = false;
            if (btn) btn.disabled = false;
        }
    }

    async function openChat(path, name = '') {
        if (!path || opening || sending || unsupported) return;
        opening = true;
        setListBusy(true);
        selectedPath = path;
        selectedName = name || selectedName;
        $('consoleChatTitle').textContent = selectedName || '聊天区域';
        clearChatArea();
        showChatPane(true);
        setStatus('正在切换聊天');
        try {
            const r = await consoleApi('/api/console/open-chat', { path });
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok || !r.data?.available) { setStatus(r.data?.detail || r.error || '切换聊天失败', true); renderError(r.data?.detail || r.error); return; }
            renderList(r.data.chat_list);
            await loadChatlogMessages(selectedName);
            setStatus('聊天已切换，聊天记录来自 Chatlog');
        } finally {
            opening = false;
            setListBusy(false);
        }
    }

    async function send() {
        if (sending || unsupported) return;
        const payload = composer?.getPayload({ attachment: '请选择文件' });
        if (!payload) return;
        if (!selectedName) {
            setStatus('请先选择聊天', true);
            return;
        }
        payload.chat_name = selectedName;
        sending = true;
        const btn = $('consoleSendBtn');
        btn.disabled = true;
        setStatus('正在发送消息');
        try {
            const r = await consoleApi('/api/console/send-message', payload);
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok || !r.data?.available) { setStatus(r.data?.detail || r.error || '发送失败', true); renderError(r.data?.detail || r.error); return; }
            if (payload.message_type === 'text' || payload.message_type === 'voice') $('consoleMsgInput').value = '';
            if (!['text', 'voice'].includes(payload.message_type)) composer.resetUpload();
            renderList(r.data.chat_list);
            setTimeout(() => {
                if (selectedName) loadChatlogMessages(selectedName, { quiet: true });
            }, 1000);
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
                if (selectedName) await loadChatlogMessages(selectedName, { preserveScroll: true, quiet: true });
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
            composer = new MessageComposer({
                typeId: 'consoleMsgType',
                textGroupId: 'consoleTextInput',
                textInputId: 'consoleMsgInput',
                attachmentGroupId: 'consoleFileInput',
                fileInputId: 'consoleFileSelect',
                dropzoneId: 'consoleFileDropzone',
                previewId: 'consoleFilePreview',
                infoId: 'consoleFileInfo',
                isActive: () => $('console-panel')?.classList.contains('active'),
            });
            $('consoleMsgInput').addEventListener('keydown', e => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
            });
            $('consoleBackBtn')?.addEventListener('click', () => showChatPane(false));
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
