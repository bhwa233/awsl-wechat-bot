let chatlogGroups = [];
let chatlogBackend = null;
let selectedChatlogGroup = null;
let groupMapping = {};
let chatFileState = { message: '', filename: '' };
let chatPage = 0;
const chatPageSize = 100;
let loadGen = 0;

function onGroupsLoaded() {
    populateSendWindowSelect();
}

function onTokenSaved() {
    loadGroups();
    chatlogBackend = null;
    detectChatlogBackend();
}

function onTabSwitch(name) {
    if (name === 'chat' && !chatlogBackend) detectChatlogBackend();
    if (name === 'tasks' && typeof taskCrud !== 'undefined') { taskCrud.load(); loadTaskServerInfo(); }
    if (name === 'webhooks' && typeof webhookCrud !== 'undefined') webhookCrud.load();
}

function populateChatGroupSelect() {
    const sel = $('chatGroupSelect');
    const prev = sel.value;
    const activeNames = new Set((allGroups || []).filter(g => g.active).map(g => g.name));
    const sorted = [...chatlogGroups].sort((a, b) => {
        const na = a.display_name || a.nick_name || a.username;
        const nb = b.display_name || b.nick_name || b.username;
        const ha = activeNames.has(na) ? 0 : 1;
        const hb = activeNames.has(nb) ? 0 : 1;
        return ha - hb;
    });
    sel.innerHTML = `<option value="">选择群聊...</option>`
        + sorted.map(g => {
            const name = g.display_name || g.nick_name || g.username;
            const active = activeNames.has(name);
            return `<option value="${escapeHtml(g.username)}">${active ? '● ' : ''}${escapeHtml(name)}</option>`;
        }).join('');
    if (prev) sel.value = prev;
}

function onChatGroupSelect() {
    const username = $('chatGroupSelect').value;
    if (!username || username === selectedChatlogGroup) return;
    selectChatlogGroup(username);
}

async function detectChatlogBackend() {
    try {
        const r = await api('GET', '/api/chatlog/health');
        if (r.ok && r.data) {
            chatlogBackend = r.data.backend || 'v1';
            $('decryptBtn').style.display = chatlogBackend === 'v1' ? '' : 'none';
        }
    } catch {}
    await loadChatlogGroups();
}

async function loadChatlogGroups() {
    const r = await api('GET', '/api/chatlog/groups');
    if (!r.ok) return;
    chatlogGroups = r.data || [];
    buildGroupMapping();
    populateChatGroupSelect();
}

function buildGroupMapping() {
    groupMapping = {};
    for (const cg of chatlogGroups) {
        const name = cg.display_name || cg.nick_name || cg.username;
        groupMapping[cg.username] = name;
    }
}

function populateSendWindowSelect() {
    const sel = $('sendWindowSelect');
    sel.innerHTML = `<option value="">发送到...</option>`
        + allGroups.filter(g => g.active).map(g =>
            `<option value="${escapeHtml(g.name)}">${escapeHtml(g.name)}</option>`
        ).join('');
}

function findGatewayWindow(chatlogDisplayName) {
    return allGroups.find(g => g.name === chatlogDisplayName && g.active);
}

function selectChatlogGroup(username) {
    selectedChatlogGroup = username;
    chatPage = 0;
    const emptyEl = $('chatEmpty');
    if (emptyEl) emptyEl.style.display = 'none';
    const selEl = $('chatlogSelect');
    if (selEl) selEl.style.display = 'none';

    const displayName = groupMapping[username] || username;
    const gw = findGatewayWindow(displayName);
    const sel = $('sendWindowSelect');
    sel.value = gw ? gw.name : '';
    updateSendState();

    loadMessages(username);
}

function updateSendState() {
    const hasTarget = !!$('sendWindowSelect').value;
    $('chatInputRow2').classList.toggle('disabled', !hasTarget);
}

async function loadMessages(chatlogId) {
    const gen = ++loadGen;
    const params = new URLSearchParams({
        group: chatlogId,
        limit: String(chatPageSize + 1),
        offset: String(chatPage * chatPageSize),
        desc: 'true',
    });
    $('messages').innerHTML = '<div class="loading-hint">加载中...</div>';
    const r = await api('GET', `/api/chatlog/messages?${params}`);
    if (gen !== loadGen) return;
    if (!r.ok) {
        $('messages').innerHTML = `<div class="empty-hint">${r.error || '加载失败'}</div>`;
        renderPagination(0, false);
        return;
    }
    const msgs = r.data || [];
    const hasMore = msgs.length > chatPageSize;
    if (hasMore) msgs.pop();
    msgs.reverse();
    renderMessages(msgs);
    renderPagination(msgs.length, hasMore);
}

function renderPagination(count, hasMore) {
    let el = $('chatPagination');
    if (!el) return;
    const hasPrev = chatPage > 0;
    if (!hasPrev && !hasMore) { el.style.display = 'none'; return; }
    el.style.display = '';
    el.innerHTML =
        `<button class="btn btn-ghost btn-sm" ${hasMore ? '' : 'disabled'} onclick="chatPageNext()">◀</button>`
        + `<span class="page-info">第 ${chatPage + 1} 页</span>`
        + `<button class="btn btn-ghost btn-sm" ${hasPrev ? '' : 'disabled'} onclick="chatPagePrev()">▶</button>`;
}

function chatPagePrev() {
    if (chatPage <= 0 || !selectedChatlogGroup) return;
    chatPage--;
    loadMessages(selectedChatlogGroup);
}

function chatPageNext() {
    if (!selectedChatlogGroup) return;
    chatPage++;
    loadMessages(selectedChatlogGroup);
}

const MSG_TYPE = { TEXT: 1, IMAGE: 3, VOICE: 34, VIDEO: 43, ANIMATION: 47, LOCATION: 48, SHARE: 49, SYSTEM: 10000, REVOKE: 10002 };

function formatMsgContent(m) {
    const type = m.msg_type;
    const content = m.content || '';

    if (type === MSG_TYPE.SYSTEM || type === MSG_TYPE.REVOKE) {
        const text = content.startsWith('<') ? (extractXmlTag(content, 'content') || extractXmlTag(content, 'plain') || content.replace(/<[^>]+>/g, '').trim()) : content;
        return `<div class="msg-system">${escapeHtml(text)}</div>`;
    }

    if (type === MSG_TYPE.IMAGE)
        return `<div class="msg-text msg-media">[图片]</div>`;
    if (type === MSG_TYPE.VOICE)
        return `<div class="msg-text msg-media">[语音]</div>`;
    if (type === MSG_TYPE.VIDEO)
        return `<div class="msg-text msg-media">[视频]</div>`;
    if (type === MSG_TYPE.ANIMATION)
        return `<div class="msg-text msg-media">[动画表情]</div>`;
    if (type === MSG_TYPE.LOCATION)
        return `<div class="msg-text msg-media">[位置]</div>`;

    if (type === MSG_TYPE.SHARE) {
        const innerType = extractXmlTag(content, 'type');
        if (innerType === '57') {
            const text = extractXmlTag(content, 'title') || '';
            const refMsg = extractXmlTag(content, 'refermsg');
            let refContent = refMsg ? extractXmlTag(refMsg, 'content') : '';
            const refName = refMsg ? extractXmlTag(refMsg, 'displayname') : '';
            if (refContent.startsWith('&lt;') || refContent.startsWith('<msg>') || refContent.startsWith('<?xml')) {
                const decoded = refContent.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"');
                refContent = extractXmlTag(decoded, 'title') || decoded.replace(/<[^>]+>/g, '').trim();
            }
            let html = '';
            if (refContent) {
                const quote = refName ? `${refName}: ${refContent}` : refContent;
                html += `<div class="msg-quote">${escapeHtml(quote)}</div>`;
            }
            html += `<div class="msg-text">${escapeHtml(text)}</div>`;
            return html;
        }
        const title = extractXmlTag(content, 'title') || '[链接]';
        const url = extractXmlTag(content, 'url');
        if (url) return `<div class="msg-text msg-link"><a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(title)}</a></div>`;
        return `<div class="msg-text msg-media">${escapeHtml(title)}</div>`;
    }

    return `<div class="msg-text">${escapeHtml(content)}</div>`;
}

function extractXmlTag(xml, tag) {
    const m = xml.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`));
    return m ? m[1].replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1').trim() : '';
}

function renderMessages(msgs) {
    const box = $('messages');
    if (!msgs.length) {
        box.innerHTML = '<div class="empty-hint">暂无消息</div>';
        return;
    }
    let lastDate = '';
    let html = '';
    for (const m of msgs) {
        const date = m.time.split('T')[0];
        if (date !== lastDate) {
            html += `<div class="msg-date">${date}</div>`;
            lastDate = date;
        }
        const time = formatTime(m.time);
        const type = m.msg_type;

        if (type === MSG_TYPE.SYSTEM || type === MSG_TYPE.REVOKE) {
            html += `<div class="msg system">${formatMsgContent(m)}</div>`;
            continue;
        }

        if (m.is_self) {
            html += `<div class="msg self"><div class="msg-bubble self">${formatMsgContent(m)}<div class="msg-time">${time}</div></div></div>`;
        } else {
            const name = m.sender_name || m.sender || '?';
            html += `<div class="msg other"><div class="msg-bubble other"><div class="msg-sender">${escapeHtml(name)}</div>${formatMsgContent(m)}<div class="msg-time">${time}</div></div></div>`;
        }
    }
    box.innerHTML = html;
    box.scrollTop = box.scrollHeight;
}

function toggleChatMsgType() {
    const t = $('msgType').value;
    const isSummary = t === 'summary';
    $('chatTextInput').style.display = t === 'text' ? '' : 'none';
    $('chatFileInput').style.display = (t === 'image' || t === 'file') ? '' : 'none';
    $('sendMsgBtn').textContent = isSummary ? '生成总结' : '发送';
    if (t === 'image' || t === 'file') {
        $('chatFileSelect').accept = t === 'image' ? 'image/*' : '';
        chatFileState = { message: '', filename: '' };
        $('chatFileInfo').textContent = '';
    }
    updateSendState();
}

function onChatFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
        chatFileState.message = e.target.result.split(',')[1];
        chatFileState.filename = file.name;
        $('chatFileInfo').textContent = file.name;
    };
    reader.readAsDataURL(file);
}

async function sendChatMessage() {
    const target = $('sendWindowSelect').value;
    if (!target) { toast('请选择发送目标窗口', 'error'); return; }
    const type = $('msgType').value;
    const btn = $('sendMsgBtn');
    btn.disabled = true;

    if (type === 'summary') {
        if (!selectedChatlogGroup) { toast('请先选择群聊', 'error'); btn.disabled = false; return; }
        btn.textContent = '生成中...';
        const r = await api('POST', '/api/summary', { body: { groups: [{ group_id: selectedChatlogGroup, group_name: target }] } });
        btn.disabled = false;
        btn.textContent = '生成总结';
        if (r.ok && r.data?.success) toast(r.data.message || '总结已启动', 'success');
        else toast(r.data?.message || r.data?.detail || r.error || '失败', 'error');
        return;
    }

    let body;
    if (type === 'text') {
        const msg = $('msgInput').value.trim();
        if (!msg) { btn.disabled = false; return; }
        body = { group_name: target, message_type: 'text', message: msg };
    } else {
        if (!chatFileState.message) { toast('请选择文件', 'error'); btn.disabled = false; return; }
        body = { group_name: target, message_type: type, message: chatFileState.message };
        if (type === 'file') body.filename = chatFileState.filename;
    }

    const r = await api('POST', '/api/send', { body });
    btn.disabled = false;
    if (r.ok) {
        if (type === 'text') $('msgInput').value = '';
        else { chatFileState = { message: '', filename: '' }; $('chatFileInfo').textContent = ''; $('chatFileSelect').value = ''; }
        toast('已发送', 'success');
        if (selectedChatlogGroup) setTimeout(() => loadMessages(selectedChatlogGroup), 1000);
    } else {
        toast(r.data?.detail || r.error || '发送失败', 'error');
    }
}

async function decryptChatlog() {
    const btn = $('decryptBtn');
    btn.disabled = true;
    btn.textContent = '解密中...';
    const r = await api('POST', '/api/chatlog/decrypt');
    btn.disabled = false;
    btn.textContent = '解密';
    if (r.ok) {
        toast(r.data?.message || '解密完成', 'success');
        await loadChatlogGroups();
    } else {
        toast(r.data?.detail || r.error || '解密失败', 'error');
    }
}

function refreshCurrentChat() {
    if (!selectedChatlogGroup) return;
    chatPage = 0;
    loadMessages(selectedChatlogGroup);
}

$('msgInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
});
$('chatGroupSelect').addEventListener('change', onChatGroupSelect);
$('chatGroupSelect').addEventListener('input', onChatGroupSelect);
