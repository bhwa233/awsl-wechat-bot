// ============================================================================
// console.js — 控制台模块：远程控制 Windows 微信主窗口，聊天记录来自 Chatlog
// 数据走 /api/console/* (Cortex 代理 Gateway)，仅 Windows Gateway 可用。
// ============================================================================

const CONSOLE_POLL_INTERVAL_MS = 3000;

window.consoleTab = (() => {
    let selectedPath = '';
    let selectedName = '';
    let opening = false;
    let refreshing = false;
    let scrolling = false;
    let sending = false;
    let standaloneOperating = false;
    let standaloneOperation = '';
    let standaloneKnown = false;
    let standaloneOpen = false;
    let standaloneCacheExists = false;
    let polling = false;
    let pollTimer = null;
    let activated = false;
    let unsupported = false;
    let composer = null;
    let historyLoadGen = 0;
    let standaloneConfirmResolver = null;
    let standaloneConfirmTrigger = null;

    function showChatPane(show) {
        $('console-panel')?.classList.toggle('console-chat-open', show);
    }

    function isConsoleMobile() {
        return window.matchMedia && window.matchMedia('(max-width: 720px)').matches;
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

    async function consoleApi(path, body = {}) {
        return api('POST', path, { body });
    }

    function selectedChatId() {
        return findChatlogGroup(selectedName)?.username || '';
    }

    function updateStandaloneControls() {
        const openBtn = $('consoleWindowOpenBtn');
        const closeBtn = $('consoleWindowCloseBtn');
        const cacheBtn = $('consoleWindowCacheBtn');
        const restoreBtn = $('consoleWindowRestoreBtn');
        const managerBtn = $('consoleWindowManagerBtn');
        if (!openBtn || !closeBtn || !cacheBtn || !restoreBtn || !managerBtn) return;

        const chatId = selectedChatId();
        const busy = standaloneOperating || opening || refreshing || scrolling || sending;
        const selected = standaloneKnown && !!selectedName && !!selectedPath && !!chatId;
        openBtn.disabled = busy || !selected || standaloneOpen || unsupported;
        closeBtn.disabled = busy || !selected || !standaloneOpen || unsupported;
        cacheBtn.disabled = busy || unsupported || !standaloneKnown;
        restoreBtn.disabled = busy || unsupported || !standaloneKnown || !standaloneCacheExists;
        managerBtn.disabled = busy || unsupported || !standaloneKnown;
        if ($('consoleRefreshBtn')) $('consoleRefreshBtn').disabled = busy || unsupported;
        if ($('consoleScrollUp')) $('consoleScrollUp').disabled = busy || unsupported;
        if ($('consoleScrollDown')) $('consoleScrollDown').disabled = busy || unsupported;
        if ($('consoleSendBtn')) $('consoleSendBtn').disabled = busy || unsupported;

        for (const [button, operation] of [
            [openBtn, 'open'],
            [closeBtn, 'close'],
            [cacheBtn, 'cache'],
            [restoreBtn, 'restore'],
        ]) {
            const loading = standaloneOperating && standaloneOperation === operation;
            button.classList.toggle('loading', loading);
            button.setAttribute('aria-busy', loading ? 'true' : 'false');
        }

        openBtn.title = !selectedName ? '请先选择聊天' : '为当前聊天打开独立窗口';
        closeBtn.title = !selectedName ? '请先选择聊天' : '关闭当前聊天的独立窗口';
        managerBtn.title = '管理全部独立窗口与全局缓存';
    }

    function standaloneWindowList(windows, emptyText) {
        if (!windows.length) return `<div class="console-window-empty">${escapeHtml(emptyText)}</div>`;
        return `<div class="console-cache-list">${windows.map(windowInfo => `
            <div class="console-cache-item">
                <span class="console-window-dot"></span>
                <strong>${escapeHtml(windowInfo.name || '未命名会话')}</strong>
            </div>`).join('')}</div>`;
    }

    function standaloneManagerVisible() {
        return $('consoleWindowManagerModal')?.style.display === 'flex';
    }

    function hideStandaloneWindowManager() {
        const modal = $('consoleWindowManagerModal');
        if (modal) modal.style.display = 'none';
    }

    async function loadStandaloneWindowManager() {
        const body = $('consoleWindowManagerBody');
        if (!body) return;
        body.innerHTML = '<div class="loading-hint">正在读取全部独立窗口与全局缓存…</div>';

        const [liveResult, cacheResult] = await Promise.all([
            api('GET', '/api/console/standalone-windows'),
            api('GET', '/api/console/standalone-windows/cache'),
        ]);
        if (liveResult.status === 501 || cacheResult.status === 501) {
            hideStandaloneWindowManager();
            showUnsupported(liveResult.data?.detail || cacheResult.data?.detail);
            return;
        }
        if (!liveResult.ok || !liveResult.data?.available || !cacheResult.ok || !cacheResult.data?.available) {
            const detail = liveResult.data?.detail || liveResult.data?.message || cacheResult.data?.detail
                || cacheResult.data?.message || liveResult.error || cacheResult.error || '读取独立窗口管理数据失败';
            body.innerHTML = `<div class="console-window-empty error">${escapeHtml(detail)}</div>`;
            setStatus(detail, true);
            return;
        }

        const liveWindows = Array.isArray(liveResult.data.windows) ? liveResult.data.windows : [];
        const cachedWindows = Array.isArray(cacheResult.data.windows) ? cacheResult.data.windows : [];
        standaloneCacheExists = !!cacheResult.data.exists;
        const updatedAt = cacheResult.data.updated_at ? formatTime(cacheResult.data.updated_at) : '尚未保存';
        body.innerHTML = `
            <div class="console-window-manager-summary">
                <span>全局窗口布局</span>
                <strong>${liveWindows.length} 个已打开 · ${cachedWindows.length} 个已缓存</strong>
            </div>
            <div class="console-window-manager-grid">
                <section class="console-window-manager-card">
                    <header><span>当前全部窗口</span><b>${liveWindows.length}</b></header>
                    ${standaloneWindowList(liveWindows, '当前没有独立窗口')}
                </section>
                <section class="console-window-manager-card">
                    <header><span>全局缓存</span><b>${cachedWindows.length}</b></header>
                    <div class="console-cache-meta">上次保存：${escapeHtml(updatedAt)}</div>
                    ${standaloneWindowList(cachedWindows, '尚未保存全局窗口布局')}
                </section>
            </div>`;
        updateStandaloneControls();
    }

    async function showStandaloneWindowManager() {
        if (standaloneOperating || opening || refreshing || scrolling || sending || unsupported) return;
        const modal = $('consoleWindowManagerModal');
        if (!modal) return;
        modal.style.display = 'flex';
        await loadStandaloneWindowManager();
    }

    async function refreshStandaloneWindowStatus({ quiet = false } = {}) {
        const chatId = selectedChatId();
        const query = chatId ? `?chat_id=${encodeURIComponent(chatId)}` : '';
        const r = await api('GET', `/api/console/standalone-windows/status${query}`);
        if (r.status === 501) { showUnsupported(r.data?.detail); return false; }
        if (!r.ok || !r.data?.available) {
            standaloneKnown = false;
            standaloneOpen = false;
            updateStandaloneControls();
            if (!quiet) setStatus(r.data?.detail || r.data?.message || r.error || '读取独立窗口状态失败', true);
            return false;
        }
        standaloneKnown = true;
        standaloneOpen = !!r.data.open;
        updateStandaloneControls();
        return true;
    }

    function closeStandaloneConfirmation(confirmed = false) {
        const modal = $('consoleWindowConfirmModal');
        if (modal) modal.style.display = 'none';
        const resolve = standaloneConfirmResolver;
        standaloneConfirmResolver = null;
        resolve?.(confirmed);
        standaloneConfirmTrigger?.focus?.();
        standaloneConfirmTrigger = null;
    }

    function confirmStandaloneOperation(operation) {
        const modal = $('consoleWindowConfirmModal');
        const submit = $('consoleWindowConfirmSubmit');
        if (!modal || !submit) return Promise.resolve(false);

        const openingWindow = operation === 'open';
        const chatName = selectedName || '当前聊天';
        $('consoleWindowConfirmTitle').textContent = openingWindow ? '打开独立窗口？' : '关闭独立窗口？';
        $('consoleWindowConfirmMessage').textContent = openingWindow
            ? '微信将为这个聊天打开一个独立窗口。'
            : '微信将关闭这个聊天当前的独立窗口，聊天记录不会删除。';
        $('consoleWindowConfirmName').textContent = chatName;
        submit.textContent = openingWindow ? '打开独立窗口' : '关闭独立窗口';
        submit.className = `btn ${openingWindow ? 'btn-green' : 'btn-red'}`;
        modal.querySelector('.console-window-confirm-modal')?.classList.toggle('is-close', !openingWindow);
        standaloneConfirmTrigger = document.activeElement;
        modal.style.display = 'flex';
        window.requestAnimationFrame(() => submit.focus());
        return new Promise(resolve => { standaloneConfirmResolver = resolve; });
    }

    async function runStandaloneOperation(operation) {
        if (standaloneOperating || opening || refreshing || scrolling || sending || unsupported || !standaloneKnown) return;

        let path;
        let payload = {};
        let pendingStatus;
        if (operation === 'open') {
            const chatId = selectedChatId();
            if (!selectedPath || !chatId || standaloneOpen) return;
            path = '/api/console/standalone-windows/open';
            payload = { path: selectedPath, chat_id: chatId };
            pendingStatus = '正在打开独立窗口';
        } else if (operation === 'close') {
            const chatId = selectedChatId();
            if (!chatId || !standaloneOpen) return;
            path = '/api/console/standalone-windows/close';
            payload = { chat_id: chatId };
            pendingStatus = '正在关闭独立窗口';
        } else if (operation === 'cache') {
            path = '/api/console/standalone-windows/cache';
            pendingStatus = '正在保存全部独立窗口';
        } else if (operation === 'restore') {
            path = '/api/console/standalone-windows/open-cached';
            pendingStatus = '正在恢复全部缓存窗口';
        } else {
            return;
        }

        if ((operation === 'open' || operation === 'close') && !await confirmStandaloneOperation(operation)) return;

        standaloneOperating = true;
        standaloneOperation = operation;
        setListBusy(true);
        updateStandaloneControls();
        setStatus(pendingStatus);
        try {
            const r = await consoleApi(path, payload);
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok || !r.data?.available) {
                setStatus(r.data?.detail || r.data?.message || r.error || `${pendingStatus}失败`, true);
                return;
            }
            setStatus(r.data.message || `${pendingStatus}完成`);
        } finally {
            if (operation === 'open' || operation === 'restore') {
                const stateResult = await consoleApi('/api/console/state', {});
                if (stateResult.ok && stateResult.data?.available) renderList(stateResult.data.chat_list);
            }
            await refreshStandaloneWindowStatus({ quiet: true });
            if (standaloneManagerVisible() && (operation === 'cache' || operation === 'restore')) {
                await loadStandaloneWindowManager();
            }
            standaloneOperating = false;
            standaloneOperation = '';
            setListBusy(false);
            updateStandaloneControls();
        }
    }

    function renderList(chatList) {
        const items = chatList?.items || [];
        $('consoleChatCount').textContent = String(items.length);
        const box = $('consoleChatList');
        if (!items.length) {
            box.innerHTML = '<div class="empty-hint">未解析到聊天列表</div>';
            updateStandaloneControls();
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
        updateStandaloneControls();
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
        const wasNearBottom = isMessageListNearBottom(box);
        const oldScrollTop = box.scrollTop;
        if (!msgs.length) {
            box.innerHTML = '<div class="empty-hint">暂无 Chatlog 消息</div>';
            observeLazyMedia(box, { followBottom: true });
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
            observeLazyMedia(box);
            return;
        }
        scrollMessageListToBottom(box);
        observeLazyMedia(box, { followBottom: true });
    }

    async function loadChatlogMessages(name, options = {}) {
        const gen = ++historyLoadGen;
        if (!options.quiet) $('consoleMessages').innerHTML = '<div class="loading-hint">正在加载 Chatlog 记录…</div>';
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
        const params = new URLSearchParams({ group: group.username, limit: '101', offset: '0', desc: 'true' });
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
                if (!isConsoleMobile()) showChatPane(true);
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

    async function restoreActualConsoleState() {
        const stateResult = await consoleApi('/api/console/state', {});
        if (!stateResult.ok || !stateResult.data?.available) return false;
        selectedName = '';
        selectedPath = '';
        renderState(stateResult.data);
        if (selectedName) {
            await loadChatlogMessages(selectedName, { preserveScroll: true, quiet: true });
        }
        return true;
    }

    function renderError(detail) {
        $('consoleChatCount').textContent = '0';
        $('consoleChatList').innerHTML = '<div class="empty-hint">未解析到聊天列表</div>';
        $('consoleMessages').innerHTML = `<div class="empty-hint">${escapeHtml(detail || '请求失败')}</div>`;
    }

    function showUnsupported(detail) {
        unsupported = true;
        standaloneKnown = false;
        standaloneOpen = false;
        $('consoleChatCount').textContent = '0';
        $('consoleChatList').innerHTML = '<div class="empty-hint">仅支持 Windows</div>';
        $('consoleMessages').innerHTML = `<div class="chat-empty"><div class="chat-empty-icon">🪟</div><p>${escapeHtml(detail || 'Console 仅支持 Windows Gateway')}</p></div>`;
        setStatus('当前 Gateway 平台不支持 Console', true);
        updateStandaloneControls();
    }

    function clearChatArea() {
        $('consoleMessages').innerHTML = '<div class="loading-hint">正在切换聊天…</div>';
    }

    function setListBusy(busy) {
        $('consoleChatList').querySelectorAll('.console-row').forEach(row => { row.disabled = busy; });
        updateStandaloneControls();
    }

    async function refresh() {
        if (refreshing || scrolling || standaloneOperating || unsupported) return;
        refreshing = true;
        updateStandaloneControls();
        const btn = $('consoleRefreshBtn');
        if (btn) btn.disabled = true;
        setStatus('正在读取主窗口 UIA 状态');
        const box = $('consoleMessages');
        if (box && !$('consoleChatList')?.children.length) {
            box.innerHTML = '<div class="loading-hint">正在读取主窗口状态…</div>';
        }
        try {
            const r = await consoleApi('/api/console/state', {});
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok) { setStatus(r.data?.detail || r.error || `请求失败 ${r.status}`, true); renderError(r.data?.detail || r.error); return; }
            renderState(r.data);
            if (selectedName) await loadChatlogMessages(selectedName, { preserveScroll: true, quiet: true });
            await refreshStandaloneWindowStatus({ quiet: true });
            setStatus(r.data.available ? '主窗口状态已更新，聊天记录来自 Chatlog' : (r.data.message || '主窗口不可用'), !r.data.available);
        } catch {
            setStatus('网络错误，无法读取 console 状态', true);
        } finally {
            refreshing = false;
            if (btn) btn.disabled = false;
            updateStandaloneControls();
        }
    }

    async function scroll(direction) {
        if (scrolling || opening || refreshing || sending || standaloneOperating || unsupported) return;
        scrolling = true;
        setListBusy(true);
        const upBtn = $('consoleScrollUp');
        const downBtn = $('consoleScrollDown');
        upBtn.disabled = true;
        downBtn.disabled = true;
        setStatus(direction === 'up' ? '正在向上滚动聊天列表' : '正在向下滚动聊天列表');
        try {
            const r = await consoleApi('/api/console/scroll-chats', { direction });
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok || !r.data?.available) { setStatus(r.data?.detail || r.error || '滚动聊天列表失败', true); return; }
            renderList(r.data.chat_list);
            setStatus(direction === 'up' ? '聊天列表已向上滚动' : '聊天列表已向下滚动');
        } catch {
            setStatus('网络错误，无法滚动聊天列表', true);
        } finally {
            scrolling = false;
            setListBusy(false);
            upBtn.disabled = false;
            downBtn.disabled = false;
        }
    }

    async function openChat(path, name = '') {
        if (!path || opening || refreshing || scrolling || sending || standaloneOperating || unsupported) return;
        opening = true;
        setListBusy(true);
        selectedPath = path;
        selectedName = name || selectedName;
        standaloneKnown = false;
        standaloneOpen = false;
        updateStandaloneControls();
        $('consoleChatTitle').textContent = selectedName || '聊天区域';
        clearChatArea();
        showChatPane(true);
        setStatus('正在切换聊天');
        try {
            const r = await consoleApi('/api/console/open-chat', { path });
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok || !r.data?.available) {
                setStatus(r.data?.detail || r.error || '切换聊天失败', true);
                if (!await restoreActualConsoleState()) renderError(r.data?.detail || r.error);
                return;
            }
            renderList(r.data.chat_list);
            await loadChatlogMessages(selectedName);
            setStatus('聊天已切换，聊天记录来自 Chatlog');
        } finally {
            if (!unsupported) await refreshStandaloneWindowStatus({ quiet: true });
            opening = false;
            setListBusy(false);
            updateStandaloneControls();
        }
    }

    async function send() {
        if (sending || opening || refreshing || scrolling || standaloneOperating || unsupported) return;
        const payload = composer?.getPayload({ attachment: '请选择文件' });
        if (!payload) return;
        if (!selectedName) { setStatus('请先选择聊天', true); return; }
        payload.chat_name = selectedName;
        sending = true;
        updateStandaloneControls();
        const btn = $('consoleSendBtn');
        btn.disabled = true;
        btn.classList.add('loading');
        btn.setAttribute('aria-busy', 'true');
        $('consoleCompose').classList.add('sending');
        setStatus('正在发送消息');
        try {
            const r = await consoleApi('/api/console/send-message', payload);
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok || !r.data?.available) { setStatus(r.data?.detail || r.error || '发送失败', true); renderError(r.data?.detail || r.error); return; }
            if (payload.message_type === 'text' || payload.message_type === 'voice') $('consoleMsgInput').value = '';
            if (!['text', 'voice'].includes(payload.message_type)) composer.resetUpload();
            renderList(r.data.chat_list);
            setTimeout(() => { if (selectedName) loadChatlogMessages(selectedName, { quiet: true }); }, 1000);
            setStatus('消息已发送');
        } finally {
            sending = false;
            btn.disabled = false;
            btn.classList.remove('loading');
            btn.setAttribute('aria-busy', 'false');
            $('consoleCompose').classList.remove('sending');
            updateStandaloneControls();
        }
    }

    function shouldPoll() {
        return activated
            && !unsupported
            && document.visibilityState === 'visible'
            && $('section-console')?.classList.contains('active')
            && !opening && !refreshing && !scrolling && !sending && !standaloneOperating;
    }

    async function poll() {
        if (polling || !shouldPoll()) return;
        polling = true;
        try {
            const r = await consoleApi('/api/console/state', {});
            if (r.status === 501) { showUnsupported(r.data?.detail); return; }
            if (!r.ok || !r.data?.available) return;
            await refreshStandaloneWindowStatus({ quiet: true });
            if (selectedPath || selectedName) {
                renderList(r.data.chat_list);
                if (selectedName) await loadChatlogMessages(selectedName, { preserveScroll: true, quiet: true });
                return;
            }
            renderState(r.data);
        } catch {
            // 轮询失败静默
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
            if (isConsoleMobile()) showChatPane(false);
            composer = new MessageComposer({
                typeId: 'consoleMsgType',
                textGroupId: 'consoleTextInput',
                textInputId: 'consoleMsgInput',
                attachmentGroupId: 'consoleFileInput',
                fileInputId: 'consoleFileSelect',
                dropzoneId: 'consoleFileDropzone',
                previewId: 'consoleFilePreview',
                infoId: 'consoleFileInfo',
                isActive: () => $('section-console')?.classList.contains('active'),
            });
            $('consoleMsgInput').addEventListener('keydown', e => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
            });
            $('consoleBackBtn')?.addEventListener('click', () => showChatPane(false));
            $('consoleScrollUp')?.addEventListener('click', () => scroll('up'));
            $('consoleScrollDown')?.addEventListener('click', () => scroll('down'));
            $('consoleWindowOpenBtn')?.addEventListener('click', () => runStandaloneOperation('open'));
            $('consoleWindowCloseBtn')?.addEventListener('click', () => runStandaloneOperation('close'));
            $('consoleWindowCacheBtn')?.addEventListener('click', () => runStandaloneOperation('cache'));
            $('consoleWindowRestoreBtn')?.addEventListener('click', () => runStandaloneOperation('restore'));
            $('consoleWindowManagerBtn')?.addEventListener('click', showStandaloneWindowManager);
            $('consoleWindowManagerClose')?.addEventListener('click', hideStandaloneWindowManager);
            $('consoleWindowManagerModal')?.addEventListener('click', event => {
                if (event.target.id === 'consoleWindowManagerModal') hideStandaloneWindowManager();
            });
            $('consoleWindowConfirmCancel')?.addEventListener('click', () => closeStandaloneConfirmation());
            $('consoleWindowConfirmSubmit')?.addEventListener('click', () => closeStandaloneConfirmation(true));
            $('consoleWindowConfirmModal')?.addEventListener('click', event => {
                if (event.target.id === 'consoleWindowConfirmModal') closeStandaloneConfirmation();
            });
            document.addEventListener('keydown', event => {
                if (event.key === 'Escape' && standaloneConfirmResolver) closeStandaloneConfirmation();
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
