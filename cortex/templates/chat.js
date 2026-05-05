let chatlogGroups = [];
let chatlogBackend = null;
let selectedChatlogGroup = null;
let groupMapping = {};
let chatFileState = { message: '', filename: '' };
let _recordContents = {};
let _recordSeq = 0;
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
    const cached = localStorage.getItem('chatlogGroup');
    if (cached && chatlogGroups.some(g => g.username === cached)) {
        $('chatGroupSelect').value = cached;
        selectChatlogGroup(cached);
    }
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
    localStorage.setItem('chatlogGroup', username);
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

const MSG_TYPE = { TEXT: 1, IMAGE: 3, VOICE: 34, NAMECARD: 42, VIDEO: 43, ANIMATION: 47, LOCATION: 48, SHARE: 49, SYSTEM: 10000, REVOKE: 10002 };

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
    if (type === MSG_TYPE.ANIMATION) {
        const md5Match = content.match(/md5="([0-9a-fA-F]{32})"/);
        const md5 = md5Match ? md5Match[1] : '';
        if (md5) {
            return `<div class="msg-emoji"><img data-emoticon-md5="${md5}" data-emoticon-content="${escapeHtml(content)}" alt="[动画表情]" loading="lazy"></div>`;
        }
        return `<div class="msg-text msg-media">[动画表情]</div>`;
    }
    if (type === MSG_TYPE.NAMECARD) {
        const nick = content.match(/nickname="([^"]*)"/);
        const certinfo = content.match(/certinfo="([^"]*)"/);
        let html = `<div class="msg-card namecard-card">`;
        html += `<div class="msg-card-icon">👤</div>`;
        html += `<div class="msg-card-body">`;
        html += `<div class="msg-card-title">${escapeHtml(nick ? nick[1] : '名片')}</div>`;
        if (certinfo && certinfo[1]) html += `<div class="msg-card-desc">${escapeHtml(certinfo[1])}</div>`;
        html += `</div></div>`;
        return html;
    }
    if (type === MSG_TYPE.LOCATION)
        return `<div class="msg-text msg-media">[位置]</div>`;

    if (type === MSG_TYPE.SHARE) {
        const innerType = extractXmlTag(content, 'type');
        if (innerType === '62') {
            const text = extractXmlTag(content, 'title') || '拍了拍';
            return `<div class="msg-system">${escapeHtml(text)}</div>`;
        }
        if (innerType === '51') {
            const feed = extractXmlTag(content, 'finderFeed');
            const nick = feed ? extractXmlTag(feed, 'nickname') : '';
            const desc = feed ? extractXmlTag(feed, 'desc') : '';
            let html = `<div class="msg-card finder-card">`;
            html += `<div class="msg-card-icon">📺</div>`;
            html += `<div class="msg-card-body">`;
            html += `<div class="msg-card-title">${escapeHtml(nick || '视频号')}</div>`;
            if (desc) html += `<div class="msg-card-desc">${escapeHtml(desc.length > 80 ? desc.slice(0, 80) + '…' : desc)}</div>`;
            html += `</div></div>`;
            return html;
        }
        if (innerType === '3') {
            const title = extractXmlTag(content, 'title') || '未知歌曲';
            const des = extractXmlTag(content, 'des') || '';
            const url = extractXmlTag(content, 'url');
            let html = `<div class="msg-card music-card">`;
            html += `<div class="msg-card-icon">♫</div>`;
            html += `<div class="msg-card-body">`;
            html += `<div class="msg-card-title">${escapeHtml(title)}</div>`;
            if (des) html += `<div class="msg-card-desc">${escapeHtml(des)}</div>`;
            html += `</div></div>`;
            if (url) html = `<a href="${escapeHtml(url)}" target="_blank" rel="noopener" style="text-decoration:none;color:inherit">${html}</a>`;
            return html;
        }
        if (innerType === '8')
            return `<div class="msg-text msg-media">[动画表情]</div>`;
        if (innerType === '6' || innerType === '36') {
            const title = extractXmlTag(content, 'title') || '[文件]';
            return `<div class="msg-text msg-media">${escapeHtml(title)}</div>`;
        }
        if (innerType === '19') {
            const title = extractXmlTag(content, 'title') || '[聊天记录]';
            const des = extractXmlTag(content, 'des');
            const rid = 'rec_' + (++_recordSeq);
            _recordContents[rid] = content;
            let html = `<div class="msg-card chatlog-card" onclick="showRecordDetail('${rid}')" style="cursor:pointer">`;
            html += `<div class="msg-card-icon">📋</div>`;
            html += `<div class="msg-card-body">`;
            html += `<div class="msg-card-title">${escapeHtml(title)}</div>`;
            if (des) html += `<div class="msg-card-desc">${escapeHtml(des.length > 100 ? des.slice(0, 100) + '…' : des)}</div>`;
            html += `</div></div>`;
            return html;
        }
        if (innerType === '57') {
            const text = extractXmlTag(content, 'title') || '';
            const refMsg = extractXmlTag(content, 'refermsg');
            let refHtml = '';
            const refName = refMsg ? extractXmlTag(refMsg, 'displayname') : '';
            const refType = refMsg ? parseInt(extractXmlTag(refMsg, 'type') || '1', 10) : 1;
            let refContent = refMsg ? extractXmlTag(refMsg, 'content') : '';
            let isCard = false;
            if (refContent) {
                let decoded = refContent.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"');
                decoded = decoded.replace(/^[a-zA-Z0-9_]+:\s*/, '');
                if (decoded.trim().startsWith('<')) {
                    refHtml = formatMsgContent({ msg_type: refType, content: decoded });
                    isCard = true;
                } else if (refType === 1) {
                    const label = refName ? `${refName}: ${decoded}` : decoded;
                    refHtml = `<div>${escapeHtml(label)}</div>`;
                } else {
                    const typeLabels = { 3: '[图片]', 34: '[语音]', 42: '[名片]', 43: '[视频]', 47: '[动画表情]', 48: '[位置]', 49: '[链接]' };
                    const tag = typeLabels[refType] || '[消息]';
                    const label = refName ? `${refName}: ${tag}` : tag;
                    refHtml = `<div>${escapeHtml(label)}</div>`;
                }
            }
            let html = '';
            if (refHtml) {
                const nameHtml = isCard && refName ? `<div class="msg-quote-name">${escapeHtml(refName)}</div>` : '';
                html += `<div class="msg-quote">${nameHtml}${refHtml}</div>`;
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

function isSystemLike(m) {
    if (m.msg_type === MSG_TYPE.SYSTEM || m.msg_type === MSG_TYPE.REVOKE) return true;
    if (m.msg_type === MSG_TYPE.SHARE && extractXmlTag(m.content || '', 'type') === '62') return true;
    return false;
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

        if (isSystemLike(m)) {
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
    loadEmoticons(box);
    box.scrollTop = box.scrollHeight;
}

const _emoticonCache = new Map();
async function loadEmoticons(container) {
    const imgs = container.querySelectorAll('img[data-emoticon-md5]');
    for (const img of imgs) {
        const md5 = img.dataset.emoticonMd5;
        const content = img.dataset.emoticonContent.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"');
        if (_emoticonCache.has(md5)) {
            img.src = _emoticonCache.get(md5);
            continue;
        }
        try {
            const token = getToken();
            const resp = await fetch('/api/chatlog/emoticon', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
                body: JSON.stringify({ content }),
            });
            if (!resp.ok) { img.alt = '[表情加载失败]'; continue; }
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            _emoticonCache.set(md5, url);
            img.src = url;
        } catch { img.alt = '[表情加载失败]'; }
    }
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

function showRecordDetail(rid) {
    const content = _recordContents[rid];
    if (!content) return;
    const decoded = content.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"');
    const recordItem = extractXmlTag(decoded, 'recorditem') || decoded;
    const decoded2 = recordItem.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"');
    const title = extractXmlTag(content, 'title') || '聊天记录';
    const items = decoded2.match(/<dataitem[\s\S]*?<\/dataitem>/g) || [];
    let body = '';
    let lastTime = '';
    for (const item of items) {
        const name = item.match(/dataitemsource\s*=\s*"([^"]*)"/)?.[1] || extractXmlTag(item, 'sourcename') || '';
        const ts = extractXmlTag(item, 'sourcetime') || '';
        const desc = (extractXmlTag(item, 'datadesc') || '').replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCharCode(parseInt(h, 16))).replace(/&#(\d+);/g, (_, d) => String.fromCharCode(d));
        const dataType = item.match(/datatype\s*=\s*"(\d+)"/)?.[1] || '';
        if (ts) {
            const timeStr = new Date(parseInt(ts) * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
            if (timeStr !== lastTime) {
                body += `<div class="record-time-divider">${timeStr}</div>`;
                lastTime = timeStr;
            }
        }
        let content = '';
        if (desc) {
            content = escapeHtml(desc);
        } else if (dataType === '3') {
            content = '🖼️ [图片]';
        } else if (dataType === '34') {
            content = '🎤 [语音]';
        } else if (dataType === '43') {
            content = '🎬 [视频]';
        } else if (dataType === '47') {
            content = '😀 [表情]';
        } else if (dataType === '49') {
            content = '📎 [链接]';
        } else {
            const dtTitle = extractXmlTag(item, 'datatitle');
            content = dtTitle ? escapeHtml(dtTitle) : '📎 [消息]';
        }
        body += `<div class="record-msg">`;
        body += `<div class="record-name">${escapeHtml(name)}</div>`;
        body += `<div class="record-bubble">${content}</div>`;
        body += `</div>`;
    }
    if (!body) body = `<div class="empty-hint">无法解析聊天记录内容</div>`;
    let modal = document.getElementById('recordModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'recordModal';
        modal.className = 'modal-overlay';
        modal.addEventListener('click', e => { if (e.target === modal) modal.style.display = 'none'; });
        document.addEventListener('keydown', e => { if (e.key === 'Escape') modal.style.display = 'none'; });
        document.body.appendChild(modal);
    }
    modal.innerHTML = `<div class="modal-content">
        <div class="modal-header"><span>${escapeHtml(title)}</span><button onclick="document.getElementById('recordModal').style.display='none'" class="modal-close">✕</button></div>
        <div class="modal-body">${body}</div>
    </div>`;
    modal.style.display = 'flex';
}
