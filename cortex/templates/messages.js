import { h, useEffect, useLayoutEffect, useRef, useState, useApp, Button, Empty, Feedback, Modal } from './ui.js';
import { getToken, initial, formatTime, formatFileSize, safeUrl } from './api.js';

export function xmlTag(xml, tag) {
    const match = String(xml || '').match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`));
    return match ? match[1].replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1').trim() : '';
}
function decodeXml(text) {
    return text
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&')
        .replace(/&quot;/g, '"');
}
export function mediaUrl(params) {
    const token = getToken();
    return `/api/chatlog/media?${new URLSearchParams({ ...params, ...(token ? { token } : {}) })}`;
}
function LazyImage({ src, onClick, alt = '', class: className = '', onLoad, fallback, retryKey = 0 }) {
    const ref = useRef(null);
    const [visible, setVisible] = useState(false);
    const [failed, setFailed] = useState(false);
    useEffect(() => {
        setFailed(false);
        if (!ref.current || !('IntersectionObserver' in window)) {
            setVisible(true);
            return;
        }
        const observer = new IntersectionObserver(
            (entries) => {
                if (entries.some((entry) => entry.isIntersecting)) {
                    setVisible(true);
                    observer.disconnect();
                }
            },
            { root: ref.current.closest('.messages'), rootMargin: '160px' }
        );
        observer.observe(ref.current);
        return () => observer.disconnect();
    }, [src, retryKey]);
    if (failed) return fallback || h('span', { class: 'media-fallback' }, alt || '图片不可用');
    return h('img', {
        ref,
        src: visible ? src : undefined,
        alt,
        class: className,
        onLoad,
        onError: () => setFailed(true),
        onClick,
        tabIndex: onClick ? 0 : undefined,
        role: onClick ? 'button' : undefined,
        onKeyDown: (e) => {
            if (onClick && ['Enter', ' '].includes(e.key)) {
                e.preventDefault();
                onClick();
            }
        },
    });
}
function Card({ icon, title, description, class: className = '', onClick, url }) {
    const body = h(
        'div',
        { class: `msg-card ${className}` },
        h('div', { class: 'msg-card-icon' }, icon),
        h(
            'div',
            { class: 'msg-card-body' },
            h('div', { class: 'msg-card-title' }, title),
            description && h('div', { class: 'msg-card-desc' }, description)
        )
    );
    if (onClick) return h('button', { type: 'button', class: 'message-card-button', onClick }, body);
    const href = safeUrl(url);
    return href ? h('a', { href, target: '_blank', rel: 'noopener noreferrer', class: 'file-link' }, body) : body;
}
export function MessageContent({
    message: m,
    caps = {},
    members = [],
    emoji = {},
    open,
    onMediaLoad,
    depth = 0,
    retryKey = 0,
}) {
    const content = String(m.content || '');
    const type = Number(m.msg_type);
    const tag = (name) => xmlTag(content, name);
    const text = (value) => String(value || '').replace(/\[([^\[\]]{1,8})\]/g, (match, name) => emoji[name] || match);
    if (depth > 4) return h('div', { class: 'msg-text' }, '[引用消息]');
    if ([10000, 10002].includes(type))
        return h(
            'div',
            { class: 'msg-system' },
            content.startsWith('<') ? tag('content') || tag('plain') || content.replace(/<[^>]+>/g, '') : content
        );
    if ([3, 43, 47].includes(type)) {
        const label = type === 3 ? '[图片]' : type === 43 ? '[视频]' : '[动画表情]';
        const supported = type === 3 ? caps.image : type === 43 ? caps.video_thumb : caps.sticker;
        if (!supported || (type !== 47 && (!m.server_id_str || !m.talker)))
            return h('div', { class: 'msg-text msg-media' }, label);
        const src = mediaUrl(
            type === 47 ? { msg_type: 47, content } : { msg_type: type, group: m.talker, server_id: m.server_id_str }
        );
        return h(
            'div',
            { class: 'msg-text msg-media' },
            h(LazyImage, {
                src,
                retryKey,
                alt: label,
                class: `msg-img ${type === 47 ? 'msg-sticker' : type === 43 ? 'msg-video-thumb' : ''}`,
                onLoad: onMediaLoad,
                onClick: () => open({ type: 'image', src, hd: type === 3 ? `${src}&hd=1` : src }),
            }),
            type === 43 && h('div', { class: 'msg-video-label' }, '▶ 视频')
        );
    }
    if (type === 34) {
        if (!caps.voice || !m.server_id_str) return h('div', { class: 'msg-text msg-media' }, '[语音]');
        const seconds = Math.round(Number(content.match(/voicelength="(\d+)"/)?.[1] || 0) / 1000);
        const src = mediaUrl({
            msg_type: 34,
            server_id: m.server_id_str,
            ...(m.local_id && m.create_time ? { local_id: m.local_id, create_time: m.create_time } : {}),
        });
        return h(
            'div',
            { class: 'msg-text msg-media' },
            h('audio', { class: 'msg-audio', src, preload: 'none', controls: true }),
            h('div', { class: 'msg-audio-label' }, seconds ? `🎙 ${seconds}″` : '🎙 语音')
        );
    }
    if (type === 42)
        return h(Card, {
            icon: '👤',
            title: content.match(/nickname="([^"]*)"/)?.[1] || '名片',
            description: content.match(/certinfo="([^"]*)"/)?.[1],
            class: 'namecard-card',
        });
    if (type === 50) return h(Card, { icon: '📞', title: tag('msg') || '[通话]', class: 'voip-card' });
    if (type === 48) {
        const attribute = (name) => content.match(new RegExp(`\\b${name}="([^"]*)"`))?.[1] || '';
        const title = attribute('poiname') || attribute('label') || '[位置]';
        const url =
            attribute('x') && attribute('y')
                ? `https://uri.amap.com/marker?position=${encodeURIComponent(attribute('y'))},${encodeURIComponent(attribute('x'))}&name=${encodeURIComponent(title)}`
                : '';
        return h(Card, {
            icon: '📍',
            title,
            description: attribute('label') !== title ? attribute('label') : '',
            url,
            class: 'location-card',
        });
    }
    if (type === 49) {
        const subtype = tag('type');
        const title = tag('title');
        if (subtype === '62') return h('div', { class: 'msg-system' }, title || '拍了拍');
        if (subtype === '51') {
            const feed = tag('finderFeed');
            return h(Card, {
                icon: '📺',
                title: xmlTag(feed, 'nickname') || '视频号',
                description: xmlTag(feed, 'desc').slice(0, 80),
                class: 'finder-card',
            });
        }
        if (subtype === '8') return h('div', { class: 'msg-text msg-media' }, '[动画表情]');
        if (['6', '36'].includes(subtype)) {
            const card = h(Card, {
                icon: '📄',
                title: title || '[文件]',
                description: [formatFileSize(Number(tag('totallen'))), caps.file && '点击下载']
                    .filter(Boolean)
                    .join(' · '),
                class: 'file-card',
            });
            return caps.file
                ? h(
                      'a',
                      {
                          class: 'file-link',
                          href: mediaUrl({ msg_type: 49, content }),
                          download: title,
                          target: '_blank',
                          rel: 'noopener',
                      },
                      card
                  )
                : card;
        }
        if (subtype === '19')
            return h(Card, {
                icon: '📋',
                title: title || '[聊天记录]',
                description: tag('des').slice(0, 100),
                class: 'chatlog-card',
                onClick: () => open({ type: 'record', content }),
            });
        if (subtype === '57') {
            const ref = tag('refermsg');
            const name = xmlTag(ref, 'displayname');
            const refType = Number(xmlTag(ref, 'type') || 1);
            const decoded = decodeXml(xmlTag(ref, 'content')).replace(/^[a-zA-Z0-9_]+:\s*/, '');
            const labels = {
                3: '[图片]',
                34: '[语音]',
                42: '[名片]',
                43: '[视频]',
                47: '[动画表情]',
                48: '[位置]',
                49: '[链接]',
            };
            return h(
                'div',
                null,
                decoded &&
                    h(
                        'div',
                        { class: 'msg-quote' },
                        name && h('div', { class: 'msg-quote-name' }, name),
                        decoded.trim().startsWith('<')
                            ? h(MessageContent, {
                                  message: { msg_type: refType, content: decoded },
                                  open,
                                  emoji,
                                  depth: depth + 1,
                                  retryKey,
                              })
                            : h('div', null, refType === 1 ? decoded : labels[refType] || '[消息]')
                    ),
                h('div', { class: 'msg-text' }, text(title))
            );
        }
        const kinds = {
            3: ['♫', '未知歌曲', 'music-card'],
            4: ['🎬', '[视频卡片]', 'video-card'],
            2: ['📝', '[笔记]', 'note-card'],
        };
        if (kinds[subtype]) {
            const [icon, fallback, className] = kinds[subtype];
            return h(Card, {
                icon,
                title: title || fallback,
                description: tag('des').slice(0, 100),
                url: subtype !== '2' ? tag('url') : '',
                class: className,
            });
        }
        const url = safeUrl(tag('url'));
        return h(
            'div',
            { class: 'msg-text msg-link' },
            url
                ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, title || '[链接]')
                : title || '[链接]'
        );
    }
    const labels = (m.at_users || [])
        .flatMap((username) => {
            const member = members.find((item) => item.username === username);
            return [
                ...new Set([member?.display_name, member?.remark, member?.nick_name, member?.alias].filter(Boolean)),
            ].map((label) => ({ username, label: '@' + label }));
        })
        .sort((a, b) => b.label.length - a.label.length);
    const value = text(content);
    const parts = [];
    let plain = '',
        index = 0;
    while (index < value.length) {
        const match = value[index] === '@' && labels.find((item) => value.startsWith(item.label, index));
        if (!match) {
            plain += value[index++];
            continue;
        }
        if (plain) {
            parts.push(plain);
            plain = '';
        }
        parts.push(
            h(
                'button',
                {
                    key: index,
                    type: 'button',
                    class: 'mention-link',
                    onClick: () => open({ type: 'member', username: match.username }),
                },
                match.label
            )
        );
        index += match.label.length;
    }
    if (plain) parts.push(plain);
    return h('div', { class: 'msg-text' }, parts);
}

function MemberDialog({ username, members, contacts, onClose }) {
    const member = members.find((item) => item.username === username) || contacts[username] || {};
    const title = member.display_name || member.remark || member.nick_name || username;
    const rows = [
        ['群昵称', member.display_name],
        ['微信昵称', member.nick_name],
        ['备注', member.remark],
        ['别名', member.alias],
        ['群主', member.is_owner ? '是' : '否'],
        ['wxid', username],
    ];
    return h(
        Modal,
        { title, onClose, class: 'mention-member-modal' },
        h(
            'div',
            { class: 'mention-member-hero' },
            safeUrl(member.avatar || member.big_avatar) &&
                h('img', {
                    class: 'mention-member-avatar-img',
                    src: safeUrl(member.avatar || member.big_avatar),
                    alt: title,
                })
        ),
        rows.map(([label, value]) =>
            h(
                'div',
                { class: 'mention-member-row', key: label },
                h('span', null, label),
                h('strong', null, value || '未设置')
            )
        ),
        [
            ['头像', member.avatar],
            ['原图头像', member.big_avatar],
        ].map(
            ([label, value]) =>
                safeUrl(value) &&
                h(
                    'div',
                    { class: 'mention-member-row', key: label },
                    h('span', null, label),
                    h('a', { href: safeUrl(value), target: '_blank', rel: 'noopener noreferrer' }, '查看')
                )
        )
    );
}
function RecordDialog({ content, onClose }) {
    const decoded = decodeXml(content);
    const record = decodeXml(xmlTag(decoded, 'recorditem') || decoded);
    const items = record.match(/<dataitem[\s\S]*?<\/dataitem>/g) || [];
    const labels = { 3: '🖼️ [图片]', 34: '🎤 [语音]', 43: '🎬 [视频]', 47: '😀 [表情]', 49: '📎 [链接]' };
    return h(
        Modal,
        { title: xmlTag(content, 'title') || '聊天记录', onClose },
        items.length
            ? items.map((item, i) => {
                  const ts = xmlTag(item, 'sourcetime');
                  const description = xmlTag(item, 'datadesc')
                      .replace(/&#x([0-9a-fA-F]+);/g, (_, n) =>
                          String.fromCodePoint(Math.min(0x10ffff, parseInt(n, 16)))
                      )
                      .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Math.min(0x10ffff, Number(n))));
                  return h(
                      'div',
                      { key: i },
                      ts &&
                          h(
                              'div',
                              { class: 'record-time-divider' },
                              new Date(Number(ts) * 1000).toLocaleString('zh-CN')
                          ),
                      h(
                          'div',
                          { class: 'record-msg' },
                          h(
                              'div',
                              { class: 'record-name' },
                              item.match(/dataitemsource\s*=\s*"([^"]*)"/)?.[1] || xmlTag(item, 'sourcename')
                          ),
                          h(
                              'div',
                              { class: 'record-bubble' },
                              description ||
                                  labels[item.match(/datatype\s*=\s*"(\d+)"/)?.[1]] ||
                                  xmlTag(item, 'datatitle') ||
                                  '📎 [消息]'
                          )
                      )
                  );
              })
            : h(Empty, null, '无法解析聊天记录内容')
    );
}
function ImageDialog({ src, hd, onClose }) {
    const [url, setUrl] = useState(src);
    const [hint, setHint] = useState(hd === src ? '' : '加载原图…');
    const [transform, setTransform] = useState({ scale: 1, x: 0, y: 0 });
    const drag = useRef(null);
    useEffect(() => {
        if (hd === src) return;
        let live = true;
        const image = new Image();
        image.onload = () => {
            if (live) {
                setUrl(hd);
                setHint('');
            }
        };
        image.onerror = () => {
            if (live) setHint('原图不可用，显示缩略图');
        };
        image.src = hd;
        return () => {
            live = false;
        };
    }, [src, hd]);
    const zoom = (delta) =>
        setTransform((previous) => {
            const scale = Math.max(0.5, Math.min(5, previous.scale + delta));
            return { ...previous, scale, ...(scale <= 1 ? { x: 0, y: 0 } : {}) };
        });
    return h(
        Modal,
        { title: '图片预览', onClose, class: 'image-viewer' },
        h(
            'div',
            { class: 'image-viewer-toolbar' },
            h(Button, { 'aria-label': '缩小', onClick: () => zoom(-0.25) }, '−'),
            h('span', null, `${Math.round(transform.scale * 100)}%`),
            h(Button, { 'aria-label': '放大', onClick: () => zoom(0.25) }, '+'),
            h(Button, { onClick: () => setTransform({ scale: 1, x: 0, y: 0 }) }, '重置'),
            h('a', { href: url, target: '_blank', rel: 'noopener' }, '打开原图')
        ),
        h(
            'div',
            { class: 'image-viewer-stage' },
            h('img', {
                src: url,
                alt: '图片预览',
                draggable: false,
                style: {
                    transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
                    touchAction: 'none',
                },
                onPointerDown: (e) => {
                    if (transform.scale <= 1) return;
                    drag.current = { x: e.clientX, y: e.clientY, originX: transform.x, originY: transform.y };
                    e.currentTarget.setPointerCapture(e.pointerId);
                },
                onPointerMove: (e) => {
                    if (drag.current)
                        setTransform((previous) => ({
                            ...previous,
                            x: drag.current.originX + e.clientX - drag.current.x,
                            y: drag.current.originY + e.clientY - drag.current.y,
                        }));
                },
                onPointerUp: () => {
                    drag.current = null;
                },
                onPointerCancel: () => {
                    drag.current = null;
                },
            })
        ),
        h('div', { class: 'help-text' }, hint)
    );
}

export function replyText(message) {
    if (Number(message.msg_type) === 1) return message.content || '';
    if (Number(message.msg_type) !== 49) return '';
    const xml = new DOMParser().parseFromString(message.content || '', 'text/xml');
    if (xml.querySelector('parsererror')) return '';
    const app = xml.documentElement.tagName === 'appmsg' ? xml.documentElement : xml.documentElement.querySelector(':scope > appmsg');
    if (app?.querySelector(':scope > type')?.textContent !== '57') return '';
    return app.querySelector(':scope > title')?.textContent || '';
}

export function MessageList({ resource, group, page = 0, caps = {}, id = 'messages', onReply }) {
    const { emoji, toast } = useApp();
    const [overlay, setOverlay] = useState(null);
    const [menu, setMenu] = useState(null);
    const menuRef = useRef(null);
    useLayoutEffect(() => {
        if (!menu) return;
        menuRef.current?.querySelector('button')?.focus();
        const dismiss = (event) => {
            if (event.type === 'keydown' && event.key !== 'Escape') return;
            if (event.type === 'pointerdown' && menuRef.current?.contains(event.target)) return;
            if (event.type === 'pointerdown' && event.target.closest('.message-menu-trigger')) return;
            setMenu(null);
        };
        document.addEventListener('pointerdown', dismiss);
        document.addEventListener('keydown', dismiss);
        window.addEventListener('resize', dismiss);
        return () => {
            document.removeEventListener('pointerdown', dismiss);
            document.removeEventListener('keydown', dismiss);
            window.removeEventListener('resize', dismiss);
        };
    }, [menu]);
    useEffect(() => { if (!onReply) setMenu(null); }, [onReply]);
    const ref = useRef(null);
    const following = useRef(true);
    const identity = useRef('');
    const { messages, contacts, members } = resource;
    const nextIdentity = `${group}:${page}`;
    const scrollBottom = () => {
        if (following.current && ref.current?.clientHeight) ref.current.scrollTop = ref.current.scrollHeight;
    };
    useLayoutEffect(() => {
        const observer = new ResizeObserver(scrollBottom);
        observer.observe(ref.current);
        return () => observer.disconnect();
    }, []);
    useLayoutEffect(() => {
        if (identity.current !== nextIdentity) {
            following.current = true;
            identity.current = nextIdentity;
            setOverlay(null);
            setMenu(null);
        }
        scrollBottom();
    }, [messages, nextIdentity]);
    async function copyMessage() {
        const content = menu.content;
        setMenu(null);
        try {
            if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(content);
            else {
                const input = document.createElement('textarea');
                input.value = content;
                input.readOnly = true;
                input.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
                document.body.append(input);
                try {
                    input.select();
                    if (!document.execCommand('copy')) throw new Error('复制失败');
                } finally {
                    input.remove();
                }
            }
            toast('已复制', 'success');
        } catch {
            toast('复制失败，请选中文字后复制', 'error');
        }
    }
    let lastDate = '';
    const children = [];
    for (const m of messages) {
        const key = `${m.talker || group}:${m.server_id_str && m.server_id_str !== '0' ? m.server_id_str : `${m.local_id}:${m.create_time}:${m.sort_seq || ''}`}`;
        const date = String(m.time || '').split('T')[0];
        if (date !== lastDate) {
            children.push(h('div', { class: 'msg-date', key: `date:${key}` }, date));
            lastDate = date;
        }
        const system =
            [10000, 10002].includes(Number(m.msg_type)) ||
            (Number(m.msg_type) === 49 && xmlTag(m.content, 'type') === '62');
        const name = m.sender_name || contacts[m.sender]?.remark || contacts[m.sender]?.nick_name || m.sender || '?';
        const avatar = safeUrl(contacts[m.sender]?.avatar || contacts[m.sender]?.big_avatar);
        const fallback = h('span', { class: 'msg-avatar msg-avatar-fallback' }, initial(name));
        const body = h(MessageContent, {
            message: m,
            caps,
            members,
            emoji,
            open: setOverlay,
            onMediaLoad: scrollBottom,
            retryKey: resource.mediaRevision,
        });
        const content = onReply && !system ? replyText(m) : '';
        children.push(
            h(
                'div',
                { class: `msg ${system ? 'system' : m.is_self ? 'self' : 'other'}`, key, 'data-message-id': key },
                !system &&
                    !m.is_self &&
                    h(
                        'button',
                        {
                            type: 'button',
                            class: 'msg-avatar-button',
                            'aria-label': `查看${name}`,
                            onClick: () => setOverlay({ type: 'member', username: m.sender }),
                        },
                        avatar
                            ? h(LazyImage, {
                                  src: avatar,
                                  class: 'msg-avatar msg-avatar-img',
                                  alt: name,
                                  fallback,
                                  retryKey: resource.mediaRevision,
                              })
                            : fallback
                    ),
                system
                    ? body
                    : h(
                          'div',
                          {
                              class: `msg-bubble ${m.is_self ? 'self' : 'other'}`,
                          },
                          !m.is_self && h('div', { class: 'msg-sender' }, name),
                          body,
                          h('div', { class: 'msg-time' }, formatTime(m.time), content.trim() && h('button', {
                              type: 'button', class: 'message-menu-trigger', 'aria-label': '消息操作',
                              'aria-haspopup': 'menu', 'aria-expanded': menu?.key === key,
                              onClick: (event) => {
                                  const rect = event.currentTarget.getBoundingClientRect();
                                  setMenu(menu?.key === key ? null : {
                                      key, content, x: Math.min(rect.left, window.innerWidth - 132),
                                      y: Math.min(rect.bottom + 4, window.innerHeight - 104),
                                  });
                              },
                          }, '⋯'))
                      )
            )
        );
    }
    return h(
        'div',
        { class: 'message-list-host' },
        h(Feedback, { loading: resource.loading, error: resource.error, retry: resource.reload }),
        h(
            'div',
            {
                class: 'messages',
                id,
                ref,
                onScroll: (e) => {
                    setMenu(null);
                    const el = e.currentTarget;
                    if (!el.clientHeight) return;
                    following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
                },
            },
            children.length
                ? children
                : !resource.loading && h(Empty, { icon: '☷' }, group ? '暂无消息' : '选择一个会话，查看聊天记录')
        ),
        menu && onReply && h('div', {
            class: 'message-context-menu', role: 'menu', 'aria-label': '消息操作', ref: menuRef,
            style: { left: Math.max(8, menu.x), top: Math.max(8, menu.y) },
        }, h('button', {
            type: 'button', role: 'menuitem', onClick: copyMessage,
        }, '复制'), h('button', {
            type: 'button', role: 'menuitem', onClick: () => { onReply(menu.content); setMenu(null); },
        }, '引用')),
        overlay?.type === 'member' &&
            h(MemberDialog, { ...overlay, members, contacts, onClose: () => setOverlay(null) }),
        overlay?.type === 'record' && h(RecordDialog, { ...overlay, onClose: () => setOverlay(null) }),
        overlay?.type === 'image' && h(ImageDialog, { key: overlay.hd, ...overlay, onClose: () => setOverlay(null) })
    );
}
