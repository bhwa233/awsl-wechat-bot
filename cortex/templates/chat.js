let chatlogGroups = [];
let chatlogBackend = null;
let chatlogMediaCaps = { image: false, sticker: false, video_thumb: false, voice: false, file: false };
let selectedChatlogGroup = null;
let groupMapping = {};
let chatMessageComposer = null;
let _recordContents = {};
let _recordSeq = 0;
let chatPage = 0;
const chatPageSize = 100;
let loadGen = 0;
const CHATLOG_CONTACT_BATCH_SIZE = 200;
const chatlogContactCache = new Map();
const chatlogContactPending = new Map();
const chatlogMemberCache = new Map();
const chatlogMemberPending = new Map();
const lazyMediaObservers = new WeakMap();

function observeLazyMedia(container) {
    const previous = lazyMediaObservers.get(container);
    if (previous) previous.disconnect();

    const elements = [...container.querySelectorAll('[data-lazy-src]')];
    if (!elements.length) {
        lazyMediaObservers.delete(container);
        return;
    }

    const load = element => {
        const src = element.dataset.lazySrc;
        if (!src) return;
        element.src = src;
        element.removeAttribute('data-lazy-src');
    };
    if (!('IntersectionObserver' in window)) {
        elements.forEach(load);
        return;
    }

    const observer = new IntersectionObserver(entries => {
        for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            observer.unobserve(entry.target);
            load(entry.target);
        }
    }, { root: container, threshold: 0.01 });
    lazyMediaObservers.set(container, observer);
    elements.forEach(element => observer.observe(element));
}

function onGroupsLoaded() {
    populateSendWindowSelect();
}

function onTokenSaved() {
    loadGroups();
    loadGatewayCapabilities();
    chatlogBackend = null;
    chatlogContactCache.clear();
    chatlogContactPending.clear();
    chatlogMemberCache.clear();
    chatlogMemberPending.clear();
    detectChatlogBackend();
}

function avatarInitial(name) {
    const text = String(name || '').trim();
    if (!text) return '?';
    return (Array.from(text)[0] || '?').toUpperCase();
}

function chunkArray(items, size) {
    const chunks = [];
    for (let i = 0; i < items.length; i += size) chunks.push(items.slice(i, i + size));
    return chunks;
}

async function fetchChatlogContacts(usernames) {
    if (!usernames.length) return;
    const params = new URLSearchParams({ usernames: usernames.join(',') });
    const r = await api('GET', `/api/chatlog/contacts?${params}`);
    if (!r.ok || !Array.isArray(r.data)) return;

    const found = new Set();
    for (const contact of r.data) {
        if (!contact?.username) continue;
        found.add(contact.username);
        chatlogContactCache.set(contact.username, contact);
    }
    for (const username of usernames) {
        if (!found.has(username)) chatlogContactCache.set(username, null);
    }
}

async function ensureChatlogSenderContacts(messages) {
    const senders = [...new Set((messages || [])
        .filter(m => !m.is_self && m.sender)
        .map(m => m.sender)
        .filter(sender => !chatlogContactCache.has(sender)))];
    if (!senders.length) return;

    const waits = [];
    const missing = [];
    for (const sender of senders) {
        const pending = chatlogContactPending.get(sender);
        if (pending) {
            waits.push(pending);
            continue;
        }
        missing.push(sender);
    }

    for (const chunk of chunkArray(missing, CHATLOG_CONTACT_BATCH_SIZE)) {
        const pending = fetchChatlogContacts(chunk).finally(() => {
            for (const sender of chunk) chatlogContactPending.delete(sender);
        });
        for (const sender of chunk) chatlogContactPending.set(sender, pending);
        waits.push(pending);
    }

    if (waits.length) await Promise.allSettled(waits);
}

function getChatlogSenderContact(sender) {
    const contact = chatlogContactCache.get(sender);
    return contact && typeof contact === 'object' ? contact : null;
}

function chatlogContactAvatar(contact) {
    return contact?.avatar || contact?.big_avatar || '';
}

function memberDisplayName(member) {
    return member?.display_name || member?.remark || member?.nick_name || member?.alias || member?.username || '';
}

async function ensureChatlogMembers(groupId) {
    if (!groupId) return [];
    if (chatlogMemberCache.has(groupId)) return chatlogMemberCache.get(groupId) || [];
    const pending = chatlogMemberPending.get(groupId);
    if (pending) return pending;
    const req = api('GET', `/api/chatlog/members?group=${encodeURIComponent(groupId)}`).then(r => {
        const members = r.ok && Array.isArray(r.data) ? r.data : [];
        chatlogMemberCache.set(groupId, members);
        return members;
    }).finally(() => chatlogMemberPending.delete(groupId));
    chatlogMemberPending.set(groupId, req);
    return req;
}

async function ensureChatlogMentionMembers(groupId, messages) {
    if (!(messages || []).some(m => Array.isArray(m.at_users) && m.at_users.length)) return;
    await ensureChatlogMembers(groupId);
}

function getChatlogMember(groupId, username) {
    const members = chatlogMemberCache.get(groupId) || [];
    return members.find(member => member.username === username) || null;
}

function getMentionLabelCandidates(member) {
    const names = [
        member?.display_name,
        member?.remark,
        member?.nick_name,
        member?.alias,
    ].map(item => String(item || '').trim()).filter(Boolean);
    return [...new Set(names)].sort((a, b) => b.length - a.length);
}

function showMentionMember(groupId, username) {
    const contact = getChatlogSenderContact(username);
    const member = getChatlogMember(groupId, username) || (contact ? { ...contact, display_name: '', is_owner: false } : null);
    const title = memberDisplayName(member) || username;
    const avatar = member?.avatar || member?.big_avatar || '';
    const fallback = avatarInitial(title);
    const rows = [
        ['群昵称', member?.display_name || '未设置'],
        ['微信昵称', member?.nick_name || '未获取'],
        ['备注', member?.remark || '未设置'],
        ['别名', member?.alias || '未设置'],
        ['群主', member?.is_owner ? '是' : '否'],
        ['wxid', member?.username || username],
        ['头像', member?.avatar ? { label: '查看头像', url: member.avatar } : '无'],
        ['原图头像', member?.big_avatar ? { label: '查看原图', url: member.big_avatar } : '无'],
    ];
    const body = rows
        .map(([label, value]) => {
            const valueHtml = value && typeof value === 'object'
                ? `<a href="${escapeHtml(value.url)}" target="_blank" rel="noopener">${escapeHtml(value.label)}</a>`
                : escapeHtml(value);
            return `<div class="mention-member-row"><span>${escapeHtml(label)}</span><strong>${valueHtml}</strong></div>`;
        })
        .join('');
    let modal = document.getElementById('mentionMemberModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'mentionMemberModal';
        modal.className = 'modal-overlay';
        modal.addEventListener('click', e => { if (e.target === modal) modal.style.display = 'none'; });
        document.addEventListener('keydown', e => { if (e.key === 'Escape') modal.style.display = 'none'; });
        document.body.appendChild(modal);
    }
    const avatarHtml = avatar
        ? `<img class="mention-member-avatar-img" src="${escapeHtml(avatar)}" alt="${escapeHtml(title)}" onerror="this.parentElement.classList.add('avatar-error')"><span class="mention-member-avatar-fallback">${escapeHtml(fallback)}</span>`
        : `<span class="mention-member-avatar-fallback">${escapeHtml(fallback)}</span>`;
    modal.innerHTML = `<div class="modal-content mention-member-modal">
        <div class="mention-member-hero">
            <div class="mention-member-avatar">${avatarHtml}</div>
            <div class="mention-member-title">
                <div class="mention-member-name">${escapeHtml(title)}</div>
                <div class="mention-member-id">${escapeHtml(username)}</div>
            </div>
            <button onclick="document.getElementById('mentionMemberModal').style.display='none'" class="modal-close mention-member-close">✕</button>
        </div>
        <div class="modal-body mention-member-body">${body}</div>
    </div>`;
    modal.style.display = 'flex';
}

function renderMessageAvatar(message, displayName) {
    const contact = getChatlogSenderContact(message.sender);
    const avatar = chatlogContactAvatar(contact);
    const fallback = `<span class="msg-avatar msg-avatar-fallback">${escapeHtml(avatarInitial(displayName))}</span>`;
    const inner = avatar
        ? `<span class="msg-avatar-wrap"><img class="msg-avatar msg-avatar-img" data-lazy-src="${escapeHtml(avatar)}" alt="${escapeHtml(displayName)}" onerror="this.parentElement.classList.add('avatar-error')">${fallback}</span>`
        : fallback;
    if (!message?.talker || !message?.sender) return inner;
    return `<button type="button" class="msg-avatar-button" onclick="showMentionMember('${escapeHtml(message.talker)}','${escapeHtml(message.sender)}')">${inner}</button>`;
}

function onTabSwitch(name) {
    if (name === 'chat' && !chatlogBackend) detectChatlogBackend();
    if (name === 'console' && typeof consoleTab !== 'undefined') consoleTab.activate();
    if (name === 'tasks' && typeof taskCrud !== 'undefined') { taskCrud.load(); loadTaskServerInfo(); }
    if (name === 'custom-commands' && typeof customCommandCrud !== 'undefined') {
        if (typeof remoteCommandPolicy !== 'undefined') remoteCommandPolicy.load();
        customCommandCrud.load();
    }
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
            if (r.data.media) {
                chatlogMediaCaps = {
                    image: !!r.data.media.image,
                    sticker: !!r.data.media.sticker,
                    video_thumb: !!r.data.media.video_thumb,
                    voice: !!r.data.media.voice,
                    file: !!r.data.media.file,
                };
            }
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
    await WECHAT_EMOJI_READY;
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
    Promise.allSettled([
        ensureChatlogSenderContacts(msgs),
        ensureChatlogMembers(chatlogId),
    ]).then(() => {
        if (gen !== loadGen) return;
        renderMessages(msgs);
    });
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

const MSG_TYPE = { TEXT: 1, IMAGE: 3, VOICE: 34, NAMECARD: 42, VIDEO: 43, ANIMATION: 47, LOCATION: 48, SHARE: 49, VOIP: 50, SYSTEM: 10000, REVOKE: 10002 };

let WECHAT_EMOJI = {};
const WECHAT_EMOJI_RE = /\[([^\[\]]{1,8})\]/g;

const WECHAT_EMOJI_READY = fetch('/static/wechat_emoji.json?v=202606162015')
    .then(r => r.ok ? r.json() : {})
    .then(m => { WECHAT_EMOJI = m; })
    .catch(() => {});

function replaceWechatEmoji(text) {
    return text.replace(WECHAT_EMOJI_RE, (m, name) => WECHAT_EMOJI[name] || m);
}

function renderTextWithEmoji(content) {
    return replaceWechatEmoji(escapeHtml(content));
}

function renderTextWithMentions(content, message) {
    const users = Array.isArray(message?.at_users) ? message.at_users : [];
    if (!users.length || !message?.talker) return renderTextWithEmoji(content);
    const memberItems = users.map(username => {
        const member = getChatlogMember(message.talker, username);
        return { username, labels: getMentionLabelCandidates(member) };
    }).filter(item => item.labels.length);
    if (!memberItems.length) return renderTextWithEmoji(content);

    const text = replaceWechatEmoji(content || '');
    let html = '';
    let i = 0;
    while (i < text.length) {
        let matched = null;
        if (text[i] === '@') {
            for (const item of memberItems) {
                for (const label of item.labels) {
                    if (text.slice(i + 1, i + 1 + label.length) === label) {
                        matched = { ...item, label };
                        break;
                    }
                }
                if (matched) break;
            }
        }
        if (matched) {
            const raw = '@' + matched.label;
            html += `<button type="button" class="mention-link" onclick="showMentionMember('${escapeHtml(message.talker)}','${escapeHtml(matched.username)}')">${escapeHtml(raw)}</button>`;
            i += raw.length;
            continue;
        }
        html += escapeHtml(text[i]);
        i++;
    }
    return html;
}

function formatMsgContent(m) {
    const type = m.msg_type;
    const content = m.content || '';

    if (type === MSG_TYPE.SYSTEM || type === MSG_TYPE.REVOKE) {
        const text = content.startsWith('<') ? (extractXmlTag(content, 'content') || extractXmlTag(content, 'plain') || content.replace(/<[^>]+>/g, '').trim()) : content;
        return `<div class="msg-system">${escapeHtml(text)}</div>`;
    }

    if (type === MSG_TYPE.VOIP) {
        // <voipmsg><VoIPBubbleMsg><msg>对方已取消 / 已在其它设备接听 / 通话时长 X 秒</msg>...
        const inner = extractXmlTag(content, 'msg') || '[通话]';
        return `<div class="msg-card voip-card"><div class="msg-card-icon">📞</div><div class="msg-card-body"><div class="msg-card-title">${escapeHtml(inner)}</div></div></div>`;
    }

    if (type === MSG_TYPE.IMAGE)
        return renderMediaImage(m, '[图片]');
    if (type === MSG_TYPE.VOICE)
        return renderMediaVoice(m, '[语音]');
    if (type === MSG_TYPE.VIDEO)
        return renderMediaVideoThumb(m, '[视频]');
    if (type === MSG_TYPE.ANIMATION)
        return renderMediaSticker(m, '[动画表情]');
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
    if (type === MSG_TYPE.LOCATION) {
        // <location label="..." poiname="..." x="..." y="..."/>
        const labelM = content.match(/\blabel="([^"]*)"/);
        const poiM = content.match(/\bpoiname="([^"]*)"/);
        const xM = content.match(/\bx="([^"]*)"/);
        const yM = content.match(/\by="([^"]*)"/);
        const poi = poiM ? poiM[1] : '';
        const label = labelM ? labelM[1] : '';
        const title = poi || label || '[位置]';
        const desc = poi && label && poi !== label ? label : '';
        const mapUrl = (xM && yM) ? `https://uri.amap.com/marker?position=${encodeURIComponent(yM[1])},${encodeURIComponent(xM[1])}&name=${encodeURIComponent(title)}` : '';
        let html = `<div class="msg-card location-card">`;
        html += `<div class="msg-card-icon">📍</div>`;
        html += `<div class="msg-card-body">`;
        html += `<div class="msg-card-title">${escapeHtml(title)}</div>`;
        if (desc) html += `<div class="msg-card-desc">${escapeHtml(desc)}</div>`;
        html += `</div></div>`;
        if (mapUrl) html = `<a href="${mapUrl}" target="_blank" rel="noopener" style="text-decoration:none;color:inherit">${html}</a>`;
        return html;
    }

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
            const totallen = extractXmlTag(content, 'totallen');
            const size = totallen ? formatFileSize(parseInt(totallen, 10)) : '';
            const ext = (title.match(/\.([^.]+)$/) || [, ''])[1].toLowerCase();
            if (!chatlogMediaCaps.file) {
                return `<div class="msg-card file-card"><div class="msg-card-icon">📄</div><div class="msg-card-body"><div class="msg-card-title">${escapeHtml(title)}</div>${size ? `<div class="msg-card-desc">${size}</div>` : ''}</div></div>`;
            }
            const url = `/api/chatlog/media?msg_type=49&content=${encodeURIComponent(content)}${mediaTokenSuffix()}`;
            return `<a href="${url}" download="${escapeHtml(title)}" target="_blank" rel="noopener" class="file-link"><div class="msg-card file-card"><div class="msg-card-icon">📄</div><div class="msg-card-body"><div class="msg-card-title">${escapeHtml(title)}</div><div class="msg-card-desc">${size}${ext ? `${size ? ' · ' : ''}${ext.toUpperCase()}` : ''} · 点击下载</div></div></div></a>`;
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
        if (innerType === '4') {
            // 视频卡片 (B 站等第三方视频分享)
            const title = extractXmlTag(content, 'title') || '[视频卡片]';
            const des = extractXmlTag(content, 'des') || '';
            const url = extractXmlTag(content, 'url');
            let html = `<div class="msg-card video-card">`;
            html += `<div class="msg-card-icon">🎬</div>`;
            html += `<div class="msg-card-body">`;
            html += `<div class="msg-card-title">${escapeHtml(title)}</div>`;
            if (des) html += `<div class="msg-card-desc">${escapeHtml(des.length > 80 ? des.slice(0, 80) + '…' : des)}</div>`;
            html += `</div></div>`;
            if (url) html = `<a href="${escapeHtml(url)}" target="_blank" rel="noopener" style="text-decoration:none;color:inherit">${html}</a>`;
            return html;
        }
        if (innerType === '2') {
            // 公众号笔记 / 收藏
            const title = extractXmlTag(content, 'title') || '[笔记]';
            const des = extractXmlTag(content, 'des') || '';
            let html = `<div class="msg-card note-card">`;
            html += `<div class="msg-card-icon">📝</div>`;
            html += `<div class="msg-card-body">`;
            html += `<div class="msg-card-title">${escapeHtml(title)}</div>`;
            if (des) html += `<div class="msg-card-desc">${escapeHtml(des.length > 80 ? des.slice(0, 80) + '…' : des)}</div>`;
            html += `</div></div>`;
            return html;
        }
        const title = extractXmlTag(content, 'title') || '[链接]';
        const url = extractXmlTag(content, 'url');
        if (url) return `<div class="msg-text msg-link"><a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(title)}</a></div>`;
        return `<div class="msg-text msg-media">${escapeHtml(title)}</div>`;
    }

    return `<div class="msg-text">${renderTextWithMentions(content, m)}</div>`;
}

function mediaPlaceholder(text) {
    return `<div class="msg-text msg-media">${escapeHtml(text)}</div>`;
}

function mediaTokenSuffix() {
    const t = getToken();
    return t ? `&token=${encodeURIComponent(t)}` : '';
}

function renderMediaImage(m, fallbackText) {
    if (!chatlogMediaCaps.image || !m.server_id_str || !m.talker) return mediaPlaceholder(fallbackText);
    // 列表用缩略图 (快, JPEG); 点击查看大图 (hd=1, 可能是 wxgf 转码)
    const tok = mediaTokenSuffix();
    const base = `/api/chatlog/media?msg_type=3&group=${encodeURIComponent(m.talker)}&server_id=${m.server_id_str}${tok}`;
    const thumbUrl = base;            // 默认 hd=false
    const hdUrl = base + '&hd=1';
    // hdUrl 经 JSON.stringify 安全嵌入 inline onclick (encodeURIComponent 不编码单引号)
    return `<div class="msg-text msg-media"><img class="msg-img" data-lazy-src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(fallbackText)}" onclick="showImageLightbox(${escapeHtml(JSON.stringify(hdUrl))}, this.src)" onerror="this.outerHTML='${escapeHtml(fallbackText)}'"></div>`;
}

function renderMediaVideoThumb(m, fallbackText) {
    if (!chatlogMediaCaps.video_thumb || !m.server_id_str || !m.talker) return mediaPlaceholder(fallbackText);
    const url = `/api/chatlog/media?msg_type=43&group=${encodeURIComponent(m.talker)}&server_id=${m.server_id_str}${mediaTokenSuffix()}`;
    return `<div class="msg-text msg-media"><img class="msg-img msg-video-thumb" data-lazy-src="${escapeHtml(url)}" alt="${escapeHtml(fallbackText)}" onclick="showImageLightbox(this.src, this.src)" onerror="this.outerHTML='${escapeHtml(fallbackText)}'"><div class="msg-video-label">▶ 视频</div></div>`;
}

function renderMediaVoice(m, fallbackText) {
    if (!chatlogMediaCaps.voice || !m.server_id_str) return mediaPlaceholder(fallbackText);
    // voicelength 单位 ms, 显示秒数
    const lenM = (m.content || '').match(/voicelength="(\d+)"/);
    const seconds = lenM ? Math.round(parseInt(lenM[1], 10) / 1000) : null;
    const label = seconds ? `🎙 ${seconds}″` : '🎙 语音';
    const fallback = m.local_id && m.create_time
        ? `&local_id=${m.local_id}&create_time=${m.create_time}`
        : '';
    const url = `/api/chatlog/media?msg_type=34&server_id=${m.server_id_str}${fallback}${mediaTokenSuffix()}`;
    return `<div class="msg-text msg-media"><audio class="msg-audio" preload="none" controls data-lazy-src="${escapeHtml(url)}"></audio><div class="msg-audio-label">${label}</div></div>`;
}

function renderMediaSticker(m, fallbackText) {
    if (!chatlogMediaCaps.sticker) return mediaPlaceholder(fallbackText);
    const content = m.content || '';
    if (!content) return mediaPlaceholder(fallbackText);
    const url = `/api/chatlog/media?msg_type=47&content=${encodeURIComponent(content)}${mediaTokenSuffix()}`;
    return `<div class="msg-text msg-media"><img class="msg-img msg-sticker" data-lazy-src="${escapeHtml(url)}" alt="${escapeHtml(fallbackText)}" onclick="showImageLightbox(this.src, this.src)" onerror="this.outerHTML='${escapeHtml(fallbackText)}'"></div>`;
}

/**
 * 大图查看: 先显示 fallbackSrc (缩略图, 立即可见), 同时拉 hdSrc, 加载完替换;
 * hdSrc 失败 (onerror) 时保留缩略图, 不报错.
 */
const imageLightboxState = {
    scale: 1, x: 0, y: 0,
    dragging: false,
    startX: 0, startY: 0, originX: 0, originY: 0,
};

function clampImageLightboxScale(scale) {
    return Math.min(5, Math.max(0.5, scale));
}

function updateImageLightboxTransform(lb) {
    const imgEl = lb.querySelector('img');
    const scaleText = lb.querySelector('.img-lightbox-scale');
    imgEl.style.transform = `translate(${imageLightboxState.x}px, ${imageLightboxState.y}px) scale(${imageLightboxState.scale})`;
    imgEl.style.cursor = imageLightboxState.scale > 1 ? (imageLightboxState.dragging ? 'grabbing' : 'grab') : 'default';
    if (scaleText) scaleText.textContent = `${Math.round(imageLightboxState.scale * 100)}%`;
}

function zoomImageLightbox(delta) {
    const lb = document.getElementById('imgLightbox');
    if (!lb || lb.style.display === 'none') return;
    const nextScale = clampImageLightboxScale(imageLightboxState.scale + delta);
    if (nextScale <= 1) {
        imageLightboxState.x = 0;
        imageLightboxState.y = 0;
    }
    imageLightboxState.scale = nextScale;
    updateImageLightboxTransform(lb);
}

function resetImageLightboxZoom() {
    const lb = document.getElementById('imgLightbox');
    if (!lb) return;
    imageLightboxState.scale = 1;
    imageLightboxState.x = 0;
    imageLightboxState.y = 0;
    imageLightboxState.dragging = false;
    updateImageLightboxTransform(lb);
}

function closeImageLightbox() {
    const lb = document.getElementById('imgLightbox');
    if (lb) lb.style.display = 'none';
}

function showImageLightbox(hdSrc, fallbackSrc) {
    let lb = document.getElementById('imgLightbox');
    if (!lb) {
        lb = document.createElement('div');
        lb.id = 'imgLightbox';
        lb.className = 'img-lightbox';
        lb.addEventListener('click', e => { if (e.target === lb) closeImageLightbox(); });
        document.addEventListener('keydown', e => { if (e.key === 'Escape') closeImageLightbox(); });
        lb.innerHTML = `
            <div class="img-lightbox-toolbar" onclick="event.stopPropagation()">
                <button type="button" class="img-lightbox-btn" onclick="zoomImageLightbox(-0.25)" aria-label="缩小" title="缩小">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m16 16 5 5"></path><path d="M8 11h6"></path></svg>
                </button>
                <span class="img-lightbox-scale">100%</span>
                <button type="button" class="img-lightbox-btn" onclick="zoomImageLightbox(0.25)" aria-label="放大" title="放大">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m16 16 5 5"></path><path d="M8 11h6"></path><path d="M11 8v6"></path></svg>
                </button>
                <button type="button" class="img-lightbox-btn" onclick="resetImageLightboxZoom()" aria-label="重置缩放" title="重置缩放">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H5a2 2 0 0 0-2 2v3"></path><path d="M16 3h3a2 2 0 0 1 2 2v3"></path><path d="M21 16v3a2 2 0 0 1-2 2h-3"></path><path d="M8 21H5a2 2 0 0 1-2-2v-3"></path><path d="M9 9h6v6H9z"></path></svg>
                </button>
            </div>
            <button type="button" class="img-lightbox-btn img-lightbox-close" onclick="closeImageLightbox()" aria-label="关闭" title="关闭">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>
            </button>
            <div class="img-lightbox-stage"><img draggable="false"></div>
            <div class="img-lightbox-hint">加载中...</div>`;
        document.body.appendChild(lb);
        const img = lb.querySelector('img');
        img.addEventListener('click', e => {
            e.stopPropagation();
        });
        img.addEventListener('pointerdown', e => {
            if (imageLightboxState.scale <= 1) return;
            imageLightboxState.dragging = true;
            imageLightboxState.startX = e.clientX;
            imageLightboxState.startY = e.clientY;
            imageLightboxState.originX = imageLightboxState.x;
            imageLightboxState.originY = imageLightboxState.y;
            img.setPointerCapture(e.pointerId);
            updateImageLightboxTransform(lb);
        });
        img.addEventListener('pointermove', e => {
            if (!imageLightboxState.dragging) return;
            imageLightboxState.x = imageLightboxState.originX + e.clientX - imageLightboxState.startX;
            imageLightboxState.y = imageLightboxState.originY + e.clientY - imageLightboxState.startY;
            updateImageLightboxTransform(lb);
        });
        img.addEventListener('pointerup', e => {
            imageLightboxState.dragging = false;
            img.releasePointerCapture(e.pointerId);
            updateImageLightboxTransform(lb);
        });
        img.addEventListener('pointercancel', () => {
            imageLightboxState.dragging = false;
            updateImageLightboxTransform(lb);
        });
    }
    const imgEl = lb.querySelector('img');
    const hint = lb.querySelector('.img-lightbox-hint');
    imgEl.src = fallbackSrc || hdSrc;
    resetImageLightboxZoom();
    lb.style.display = 'flex';
    if (!fallbackSrc || hdSrc === fallbackSrc) {
        hint.textContent = '';
        return;
    }
    hint.textContent = '加载原图...';
    const loader = new Image();
    loader.onload = () => {
        imgEl.src = hdSrc;
        hint.textContent = '';
    };
    loader.onerror = () => {
        hint.textContent = '原图不可用, 显示缩略图';
        setTimeout(() => { hint.textContent = ''; }, 2000);
    };
    loader.src = hdSrc;
}

function extractXmlTag(xml, tag) {
    const m = xml.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`));
    return m ? m[1].replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1').trim() : '';
}

function formatFileSize(bytes) {
    if (!bytes || bytes < 0) return '';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    let n = bytes;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n : n.toFixed(1)) + ' ' + units[i];
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
        observeLazyMedia(box);
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
            html += `<div class="msg other">${renderMessageAvatar(m, name)}<div class="msg-bubble other"><div class="msg-sender">${escapeHtml(name)}</div>${formatMsgContent(m)}<div class="msg-time">${time}</div></div></div>`;
        }
    }
    box.innerHTML = html;
    box.scrollTop = box.scrollHeight;
    observeLazyMedia(box);
}

function toggleChatMsgType() {
    const t = $('msgType').value;
    const isSummary = t === 'summary';
    if (chatMessageComposer) chatMessageComposer.syncType();
    $('sendMsgBtn').textContent = isSummary ? '生成总结' : '发送';
    updateSendState();
}

async function sendChatMessage() {
    const target = $('sendWindowSelect').value;
    if (!target) { toast('请选择发送目标窗口', 'error'); return; }
    const type = chatMessageComposer.getType();
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

    const payload = chatMessageComposer.getPayload({ attachment: '请选择文件' });
    if (!payload) { btn.disabled = false; return; }
    const body = { group_name: target, ...payload };

    const r = await api('POST', '/api/send', { body });
    btn.disabled = false;
    if (r.ok) {
        if (type === 'text' || type === 'voice') $('msgInput').value = '';
        if (!['text', 'voice'].includes(type)) chatMessageComposer.resetUpload();
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

chatMessageComposer = new MessageComposer({
    typeId: 'msgType',
    textGroupId: 'chatTextInput',
    textInputId: 'msgInput',
    attachmentGroupId: 'chatFileInput',
    fileInputId: 'chatFileSelect',
    dropzoneId: 'chatFileDropzone',
    previewId: 'chatFilePreview',
    infoId: 'chatFileInfo',
    isActive: () => $('chat-panel')?.classList.contains('active'),
    onTypeChange: type => {
        $('sendMsgBtn').textContent = type === 'summary' ? '生成总结' : '发送';
        updateSendState();
    },
});

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
