import { h, useEffect, useRef, useState, useApp, Button, Select, Empty, Feedback } from './ui.js';
import { api, groupName, initial } from './api.js';
import { useChatlog, useMessages } from './chatlog.js';
import { MessageList } from './messages.js';
import { MessageComposer, emptyMessage, messagePayload } from './message_composer.js';

export function Chat({ active }) {
    const { groups, capabilities, toast, refreshServer } = useApp();
    const chatlog = useChatlog();
    const [selected, setSelected] = useState('');
    const [search, setSearch] = useState('');
    const [visibleCount, setVisibleCount] = useState(40);
    const [page, setPage] = useState(0);
    const [thread, setThread] = useState(false);
    const [targetOverride, setTargetOverride] = useState(null);
    const [message, setMessage] = useState(emptyMessage);
    const [busy, setBusy] = useState(false);
    const [decrypting, setDecrypting] = useState(false);
    const lock = useRef(false);
    const sendTimer = useRef(null);
    const chosen = useRef(false);
    const all = chatlog.data?.groups || [];
    const online = groups.filter((g) => g.active);
    const onlineNames = new Set(online.map(groupName));
    const current = all.find((g) => g.username === selected);
    const name = groupName(current);
    const target = targetOverride ?? (onlineNames.has(name) ? name : '');
    const messages = useMessages(selected, page, active);
    useEffect(() => {
        if (!chatlog.data?.groups || chosen.current) return;
        chosen.current = true;
        const cached = localStorage.getItem('chatlogGroup');
        if (all.some((g) => g.username === cached)) select(cached, false);
    }, [chatlog.data]);
    useEffect(() => () => clearTimeout(sendTimer.current), []);
    function select(username, showThread = true) {
        if (lock.current) return;
        chosen.current = true;
        clearTimeout(sendTimer.current);
        setSelected(username);
        setMessage((value) => ({ ...value, reply_to_message: undefined }));
        setPage(0);
        setThread(showThread);
        localStorage.setItem('chatlogGroup', username);
        setTargetOverride(null);
    }
    async function send() {
        if (lock.current) return;
        if (!target || !onlineNames.has(target)) {
            toast('请选择发送窗口', 'error');
            return;
        }
        lock.current = true;
        setBusy(true);
        const destination = selected;
        try {
            const payload = messagePayload(message, capabilities);
            if (payload.message_type === 'summary') {
                if (!destination) throw new Error('请先选择会话');
                const data = await api('POST', '/api/summary', {
                    body: { groups: [{ group_id: destination, group_name: target }] },
                });
                if (!data?.success) throw new Error(data?.message || '总结启动失败');
                toast(data.message || '总结已启动', 'success');
            } else {
                await api('POST', '/api/send', { body: { group_name: target, ...payload } });
                setMessage((value) => ({ ...value, message: '', filename: '', reply_to_message: undefined }));
                toast('已提交发送', 'success');
                sendTimer.current = setTimeout(messages.reload, 1000);
            }
        } catch (e) {
            toast(e.message, 'error');
        } finally {
            lock.current = false;
            setBusy(false);
        }
    }
    async function decrypt() {
        if (decrypting) return;
        setDecrypting(true);
        try {
            const data = await api('POST', '/api/chatlog/decrypt', { timeout: 180000 });
            toast(data?.message || '解密完成', 'success');
            chatlog.reload();
            messages.reload();
        } catch (e) {
            toast(e.message, 'error');
        } finally {
            setDecrypting(false);
        }
    }
    const query = search.trim().toLowerCase();
    const matched = [...all]
        .sort((a, b) => Number(onlineNames.has(groupName(b))) - Number(onlineNames.has(groupName(a))))
        .filter(
            (g) => !query || groupName(g).toLowerCase().includes(query) || g.username.toLowerCase().includes(query)
        );
    const shown = matched.slice(0, visibleCount);
    return h(
        'div',
        { class: `chat-shell chat-page ${thread ? 'thread-open' : ''}` },
        h(
            'aside',
            { class: 'chat-sidebar' },
            h(
                'div',
                { class: 'chat-sidebar-head' },
                h('h3', { class: 'chat-sidebar-title' }, '会话'),
                h('span', { class: 'chat-sidebar-count' }, matched.length),
                chatlog.data?.health.backend === 'v1' &&
                    h(Button, { class: 'btn-xs', busy: decrypting, onClick: decrypt }, '解密'),
                h(
                    Button,
                    {
                        class: 'btn-xs',
                        onClick: () => {
                            chatlog.reload();
                            refreshServer();
                        },
                        'aria-label': '刷新会话列表',
                    },
                    '↻'
                )
            ),
            h(
                'div',
                { class: 'chat-sidebar-search' },
                h('span', { 'aria-hidden': true }, '⌕'),
                h('input', {
                    id: 'chatGroupSearch',
                    'aria-label': '搜索会话',
                    placeholder: '搜索会话…',
                    value: search,
                    onInput: (e) => {
                        setSearch(e.currentTarget.value);
                        setVisibleCount(40);
                    },
                })
            ),
            h(Feedback, { ...chatlog, retry: chatlog.reload }),
            h(
                'div',
                { class: 'chat-group-list', id: 'chatGroupList' },
                shown.map((g) =>
                    h(
                        'button',
                        {
                            key: g.username,
                            type: 'button',
                            class: `chat-group-item ${selected === g.username ? 'active' : ''}`,
                            'data-username': g.username,
                            disabled: busy,
                            onClick: () => select(g.username),
                        },
                        h('span', { class: 'chat-group-avatar' }, initial(groupName(g))),
                        h(
                            'span',
                            { class: 'chat-group-copy' },
                            h('span', { class: 'chat-group-name' }, groupName(g)),
                            h(
                                'span',
                                { class: 'chat-group-meta' },
                                onlineNames.has(groupName(g))
                                    ? h('span', { class: 'chat-group-online' }, '● 在线')
                                    : g.username
                            )
                        )
                    )
                ),
                matched.length > shown.length &&
                    h(
                        'button',
                        { type: 'button', class: 'chat-load-more', onClick: () => setVisibleCount((n) => n + 40) },
                        `加载更多（剩余 ${matched.length - shown.length}）`
                    ),
                !chatlog.loading && !chatlog.error && !shown.length && h(Empty, null, '无匹配会话')
            )
        ),
        h(
            'div',
            { class: 'chat-main' },
            h(
                'header',
                { class: 'chat-head' },
                h(
                    Button,
                    { class: 'chat-back-btn', 'aria-label': '返回会话列表', onClick: () => setThread(false) },
                    '‹'
                ),
                h(
                    'div',
                    { class: 'chat-head-title' },
                    h('h3', { id: 'chatTitle' }, current ? groupName(current) : '选择一个会话'),
                    h(
                        'span',
                        { class: 'chat-head-sub' },
                        current ? (onlineNames.has(groupName(current)) ? '可发送' : '聊天记录') : ''
                    )
                ),
                h(
                    Button,
                    {
                        class: 'btn-sm',
                        disabled: !selected || messages.loading,
                        onClick: () => {
                            setPage(0);
                            messages.reload();
                        },
                    },
                    '刷新'
                )
            ),
            (page > 0 || messages.hasMore) &&
                h(
                    'div',
                    { class: 'chat-pagination' },
                    h(
                        Button,
                        {
                            class: 'btn-sm',
                            disabled: !messages.hasMore || messages.loading,
                            onClick: () => setPage((n) => n + 1),
                        },
                        '↑ 更早消息'
                    ),
                    h('span', { class: 'page-info' }, `第 ${page + 1} 页 · ${messages.messages.length} 条`),
                    page > 0 &&
                        h(
                            Button,
                            { class: 'btn-sm', disabled: messages.loading, onClick: () => setPage((n) => n - 1) },
                            '较新消息'
                        ),
                    page > 0 && h(Button, { class: 'btn-sm', onClick: () => setPage(0) }, '回到最新')
                ),
            h(MessageList, {
                resource: messages, group: selected, page, caps: chatlog.data?.health.media,
                onReply: capabilities.reply && !busy && target === name ? (reply_to_message) => {
                    setMessage((value) => ({ ...emptyMessage(), message: value.message_type === 'text' ? value.message : '', reply_to_message }));
                    requestAnimationFrame(() => document.getElementById('chat-composer-text')?.focus());
                } : null,
            }),
            h(
                MessageComposer,
                {
                    id: 'chat-composer',
                    value: message,
                    onChange: setMessage,
                    onSend: send,
                    summary: true,
                    busy,
                    active,
                    disabled: !target || !onlineNames.has(target),
                },
                h(Select, {
                    id: 'sendWindowSelect',
                    class: 'send-target-select',
                    'aria-label': '发送目标',
                    value: onlineNames.has(target) ? target : '',
                    disabled: busy,
                    onValue: (target) => { setTargetOverride(target); setMessage((value) => ({ ...value, reply_to_message: undefined })); },
                    options: [['', '发送到…'], ...online.map((g) => [groupName(g), groupName(g)])],
                })
            )
        )
    );
}
