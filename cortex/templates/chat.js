let chatlogGroups = [];
let chatlogBackend = null;
let selectedChatlogGroup = null;
let groupMapping = {};
let chatFileState = { message: '', filename: '' };

function onGroupsLoaded() {
    renderGatewayList();
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

function renderChatlogList(filter = '') {
    const list = $('chatlogGroupList');
    if (!chatlogGroups.length) {
        list.innerHTML = '<div class="empty-hint">无 chatlog 群</div>';
        return;
    }
    const f = filter.toLowerCase();
    const filtered = f ? chatlogGroups.filter(g => {
        const name = g.display_name || g.nick_name || g.username;
        return name.toLowerCase().includes(f) || g.username.toLowerCase().includes(f);
    }) : chatlogGroups;
    list.innerHTML = filtered.map(g => {
        const name = g.display_name || g.nick_name || g.username;
        return `<div class="group-item${selectedChatlogGroup === g.username ? ' active' : ''}" data-username="${escapeHtml(g.username)}">`
            + `<div class="group-name">${escapeHtml(name)}</div>`
            + `</div>`;
    }).join('');
}

$('chatlogGroupList').addEventListener('click', e => {
    const item = e.target.closest('.group-item[data-username]');
    if (item) selectChatlogGroup(item.dataset.username);
});

function renderGatewayList() {
    const list = $('gatewayGroupList');
    if (!allGroups.length) {
        list.innerHTML = '<div class="empty-hint">无窗口</div>';
        return;
    }
    list.innerHTML = allGroups.map(g =>
        `<div class="group-item${g.active ? '' : ' inactive'}" style="padding:6px 14px">`
        + `<div class="group-name" style="font-size:12px">${escapeHtml(g.name)}</div>`
        + `${g.active ? '' : '<div class="group-badge">离线</div>'}`
        + `</div>`
    ).join('');
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
    renderChatlogList($('groupSearch').value);
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
    renderChatlogList($('groupSearch').value);
    $('chatHeader').style.display = '';
    $('chatInput').style.display = '';
    $('chatEmpty').style.display = 'none';
    $('chatlogSelect').style.display = 'none';

    const displayName = groupMapping[username] || username;
    $('chatGroupName').textContent = displayName;

    const gw = findGatewayWindow(displayName);
    const sel = $('sendWindowSelect');
    if (gw) {
        sel.value = gw.name;
    } else {
        sel.value = '';
    }

    loadMessages(username);
}

async function loadMessages(chatlogId) {
    const dateInput = $('chatDate');
    const params = new URLSearchParams({ group: chatlogId, limit: '500' });
    if (dateInput.value) {
        params.set('start', dateInput.value);
        params.set('end', dateInput.value);
    }
    $('messages').innerHTML = '<div class="loading-hint">加载中...</div>';
    const r = await api('GET', `/api/chatlog/messages?${params}`);
    if (!r.ok) {
        $('messages').innerHTML = `<div class="empty-hint">${r.error || '加载失败'}</div>`;
        return;
    }
    renderMessages(r.data || []);
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
        if (m.is_self) {
            html += `<div class="msg self"><div class="msg-bubble self"><div class="msg-text">${escapeHtml(m.content)}</div><div class="msg-time">${time}</div></div></div>`;
        } else {
            const name = m.sender_name || m.sender || '?';
            html += `<div class="msg other"><div class="msg-bubble other"><div class="msg-sender">${escapeHtml(name)}</div><div class="msg-text">${escapeHtml(m.content)}</div><div class="msg-time">${time}</div></div></div>`;
        }
    }
    box.innerHTML = html;
    box.scrollTop = box.scrollHeight;
}

function toggleChatMsgType() {
    const t = $('msgType').value;
    $('chatTextInput').style.display = t === 'text' ? '' : 'none';
    $('chatFileInput').style.display = t !== 'text' ? '' : 'none';
    if (t !== 'text') {
        $('chatFileSelect').accept = t === 'image' ? 'image/*' : '';
        chatFileState = { message: '', filename: '' };
        $('chatFileInfo').textContent = '';
    }
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

async function triggerSummary() {
    const target = $('sendWindowSelect').value;
    if (!selectedChatlogGroup || !target) { toast('请选择群聊和发送窗口', 'error'); return; }
    const btn = $('summaryBtn');
    btn.disabled = true;
    btn.textContent = '生成中...';
    const r = await api('POST', '/api/summary', { body: { groups: [{ group_id: selectedChatlogGroup, group_name: target }] } });
    btn.disabled = false;
    btn.textContent = '总结';
    if (r.ok && r.data?.success) toast(r.data.message || '总结已启动', 'success');
    else toast(r.data?.message || r.data?.detail || r.error || '失败', 'error');
}

function refreshCurrentChat() {
    if (!selectedChatlogGroup) return;
    loadMessages(selectedChatlogGroup);
}

function onChatDateChange() {
    if (!selectedChatlogGroup) return;
    loadMessages(selectedChatlogGroup);
}

$('groupSearch').addEventListener('input', e => renderChatlogList(e.target.value));
$('msgInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
});
