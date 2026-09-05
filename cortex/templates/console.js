import { h, useEffect, useRef, useState, usePoll, useResource, useApp, Button, Empty, Feedback, Modal } from './ui.js';
import { api, initial, formatTime } from './api.js';
import { useChatlog, useMessages } from './chatlog.js';
import { MessageList } from './messages.js';
import { MessageComposer, emptyMessage, messagePayload } from './message_composer.js';

function WindowManager({ busy, operate, onClose, revision }) {
    const resource = useResource(
        async (signal) => {
            const [live, cache] = await Promise.all([
                api('GET', '/api/console/standalone-windows', { signal }),
                api('GET', '/api/console/standalone-windows/cache', { signal }),
            ]);
            if (!live?.available || !cache?.available)
                throw new Error(live?.message || cache?.message || '窗口管理不可用');
            return { live, cache };
        },
        [revision]
    );
    const data = resource.data;
    return h(
        Modal,
        {
            title: '独立窗口管理',
            onClose,
            busy,
            class: 'console-window-manager-modal',
            footer: [
                h(Button, { disabled: !data || busy, onClick: () => operate('cache') }, '保存全部窗口'),
                h(
                    Button,
                    { variant: 'green', disabled: !data?.cache.exists || busy, onClick: () => operate('restore') },
                    '一键恢复全部'
                ),
            ],
        },
        h(Feedback, { ...resource, retry: resource.reload }),
        data &&
            h(
                'div',
                null,
                h(
                    'div',
                    { class: 'console-window-manager-summary' },
                    h('span', null, '全局窗口布局'),
                    h(
                        'strong',
                        null,
                        `${data.live.windows?.length || 0} 个已打开 · ${data.cache.windows?.length || 0} 个已缓存`
                    )
                ),
                h(
                    'div',
                    { class: 'console-window-manager-grid' },
                    [
                        ['当前全部窗口', data.live.windows],
                        ['全局缓存', data.cache.windows],
                    ].map(([title, windows]) =>
                        h(
                            'section',
                            { class: 'console-window-manager-card', key: title },
                            h('header', null, h('span', null, title), h('b', null, windows?.length || 0)),
                            windows?.length
                                ? h(
                                      'div',
                                      { class: 'console-cache-list' },
                                      windows.map((window, i) =>
                                          h(
                                              'div',
                                              { class: 'console-cache-item', key: `${window.name}:${i}` },
                                              h('span', { class: 'console-window-dot' }),
                                              h('strong', null, window.name || '未命名会话')
                                          )
                                      )
                                  )
                                : h('div', { class: 'console-window-empty' }, '暂无窗口')
                        )
                    )
                ),
                h(
                    'div',
                    { class: 'help-text' },
                    `上次保存：${data.cache.updated_at ? formatTime(data.cache.updated_at) : '尚未保存'}`
                )
            )
    );
}

export function Console({ active }) {
    const { capabilities } = useApp();
    const chatlog = useChatlog();
    const [items, setItems] = useState([]);
    const [selected, setSelected] = useState({ name: '', path: '' });
    const selectedRef = useRef(selected);
    const [thread, setThread] = useState(false);
    const [phase, setPhase] = useState('');
    const [error, setError] = useState('');
    const [status, setStatus] = useState('加载中…');
    const [unsupported, setUnsupported] = useState(false);
    const [windowStatus, setWindowStatus] = useState(null);
    const [manager, setManager] = useState(false);
    const [confirming, setConfirming] = useState('');
    const [managerRevision, setManagerRevision] = useState(0);
    const [message, setMessage] = useState(emptyMessage);
    const lock = useRef(false);
    const live = useRef(true);
    const readController = useRef(null);
    const sendTimer = useRef(null);
    const groups = chatlog.data?.groups || [];
    const findGroup = (name) => {
        const target = String(name || '').trim();
        if (!target) return null;
        return groups.find((g) =>
            [g.username, g.display_name, g.remark, g.nick_name].some((value) => String(value || '').trim() === target)
        );
    };
    const group = findGroup(selected.name);
    const messages = useMessages(group?.username || '', 0, active && !unsupported);
    const busy = !!phase || !!confirming;
    const polling = phase === 'poll' && !confirming;
    function choose(next) {
        if (next.name !== selectedRef.current.name) setMessage((value) => ({ ...value, reply_to_message: undefined }));
        selectedRef.current = next;
        setSelected(next);
    }
    function applyList(list) {
        const rows = list?.items || [];
        setItems(rows);
        const match = rows.find((item) => item.name === selectedRef.current.name);
        if (match?.path) choose({ name: match.name, path: match.path });
    }
    function applyState(data, actual = false) {
        if (!data?.available) throw new Error(data?.message || '微信主窗口不可用');
        if (actual || !selectedRef.current.name) {
            const current = data.chat_list?.items?.find((item) => item.name === data.chat_area?.title);
            choose(current ? { name: current.name, path: current.path || '' } : { name: '', path: '' });
        }
        applyList(data.chat_list);
    }
    async function updateWindowStatus(signal) {
        const name = selectedRef.current.name;
        const match = findGroup(name);
        const query = match ? `?chat_id=${encodeURIComponent(match.username)}` : '';
        try {
            const data = await api('GET', `/api/console/standalone-windows/status${query}`, { signal });
            if (!live.current || name !== selectedRef.current.name) return;
            setWindowStatus(data?.available ? data : null);
        } catch (e) {
            if (e.name !== 'AbortError' && live.current) setWindowStatus(null);
        }
    }
    async function refresh(quiet = false) {
        if (lock.current || confirming || unsupported || !active || (quiet && manager)) return;
        lock.current = true;
        setPhase(quiet ? 'poll' : 'refresh');
        const controller = new AbortController();
        readController.current = controller;
        try {
            const data = await api('POST', '/api/console/state', { body: {}, signal: controller.signal });
            if (!live.current) return;
            applyState(data);
            setError('');
            await updateWindowStatus(controller.signal);
            if (!quiet) {
                messages.reload();
                setStatus('已刷新');
            }
        } catch (e) {
            if (!live.current || e.name === 'AbortError') return;
            if (e.status === 501) {
                setUnsupported(true);
                setStatus('当前平台不支持控制台');
            }
            setError(e.message);
        } finally {
            if (readController.current === controller) readController.current = null;
            lock.current = false;
            if (live.current) setPhase('');
        }
    }
    useEffect(() => {
        live.current = true;
        return () => {
            live.current = false;
            readController.current?.abort();
            clearTimeout(sendTimer.current);
        };
    }, []);
    useEffect(() => {
        if (active) refresh();
        else readController.current?.abort();
    }, [active, chatlog.data]);
    usePoll(() => refresh(true), active && !unsupported);

    async function operate(operation, item) {
        if (lock.current || unsupported) return;
        lock.current = true;
        setPhase(operation);
        setConfirming('');
        setError('');
        const current = selectedRef.current;
        const chatId = findGroup(current.name)?.username;
        try {
            let path,
                body = {};
            if (operation === 'select') {
                path = '/api/console/open-chat';
                body = { path: item.path };
            } else if (operation === 'up' || operation === 'down') {
                path = '/api/console/scroll-chats';
                body = { direction: operation };
            } else if (operation === 'open') {
                if (!chatId || !current.path) throw new Error('请先选择已匹配的聊天');
                path = '/api/console/standalone-windows/open';
                body = { path: current.path, chat_id: chatId };
            } else if (operation === 'close') {
                if (!chatId) throw new Error('请先选择聊天');
                path = '/api/console/standalone-windows/close';
                body = { chat_id: chatId };
            } else if (operation === 'cache') path = '/api/console/standalone-windows/cache';
            else if (operation === 'restore') path = '/api/console/standalone-windows/open-cached';
            else if (operation === 'send') {
                if (!current.name) throw new Error('请先选择聊天');
                path = '/api/console/send-message';
                body = { ...messagePayload(message, { ...capabilities, reply: !unsupported }), chat_name: current.name };
            } else return;
            const data = await api('POST', path, { body, timeout: 180000 });
            if (!data?.available) throw new Error(data?.message || '控制台操作失败');
            if (!live.current) return;
            if (operation === 'select') {
                choose({ name: item.name, path: item.path });
                setThread(true);
                setWindowStatus(null);
            }
            if (data.chat_list) applyList(data.chat_list);
            if (operation === 'send') {
                setMessage((value) => ({ ...value, message: '', filename: '', reply_to_message: undefined }));
                clearTimeout(sendTimer.current);
                sendTimer.current = setTimeout(messages.reload, 1000);
            }
            if (['open', 'restore'].includes(operation)) {
                const state = await api('POST', '/api/console/state', { body: {} });
                if (state?.available) applyList(state.chat_list);
            }
            if (['cache', 'restore'].includes(operation)) setManagerRevision((n) => n + 1);
            await updateWindowStatus();
            const labels = {
                select: '聊天已切换',
                up: '列表已向上滚动',
                down: '列表已向下滚动',
                open: '独立窗口已打开',
                close: '独立窗口已关闭',
                cache: '窗口布局已保存',
                restore: '窗口布局已恢复',
                send: '发送操作已提交',
            };
            setStatus(data.message || labels[operation]);
        } catch (e) {
            if (!live.current) return;
            setError(e.message);
            if (e.status === 501) setUnsupported(true);
            if (operation === 'select') {
                try {
                    const actual = await api('POST', '/api/console/state', { body: {} });
                    if (live.current) applyState(actual, true);
                } catch {
                    choose({ name: '', path: '' });
                    setWindowStatus(null);
                }
            }
        } finally {
            lock.current = false;
            if (live.current) setPhase('');
        }
    }
    return h(
        'div',
        { class: `console-layout ${thread ? 'console-chat-open' : ''}`, id: 'console-panel' },
        h(
            'aside',
            { class: 'console-list' },
            h(
                'div',
                { class: 'console-list-head' },
                h('h3', { class: 'console-list-title' }, '聊天列表'),
                h('span', { class: 'console-count' }, items.length),
                h(
                    'div',
                    { class: 'console-list-actions' },
                    h(
                        Button,
                        {
                            class: 'btn-sm console-window-manager-trigger',
                            disabled: busy || unsupported || !windowStatus,
                            quietDisabled: polling && !unsupported && !!windowStatus,
                            onClick: () => setManager(true),
                            'aria-label': '独立窗口管理',
                            title: '独立窗口管理',
                        },
                        h(
                            'svg',
                            { viewBox: '0 0 20 20', 'aria-hidden': true },
                            h('path', { d: 'M3.5 12.5V4.75A1.75 1.75 0 0 1 5.25 3h7.25' }),
                            h('rect', { x: 6, y: 6, width: 10.5, height: 9.5, rx: 1.75 })
                        )
                    ),
                    h(
                        Button,
                        {
                            class: 'btn-sm',
                            disabled: busy || unsupported,
                            quietDisabled: polling && !unsupported,
                            onClick: () => operate('up'),
                            'aria-label': '向上滚动聊天列表',
                        },
                        '↑'
                    ),
                    h(
                        Button,
                        {
                            class: 'btn-sm',
                            disabled: busy || unsupported,
                            quietDisabled: polling && !unsupported,
                            onClick: () => operate('down'),
                            'aria-label': '向下滚动聊天列表',
                        },
                        '↓'
                    )
                )
            ),
            h(
                'div',
                { class: 'console-list-body', id: 'consoleChatList' },
                items.map((item) =>
                    h(
                        'button',
                        {
                            key: item.path || item.name,
                            type: 'button',
                            class: `console-row ${item.name === selected.name ? 'active' : ''}`,
                            'data-name': item.name,
                            disabled: busy || unsupported,
                            'data-quiet-disabled': polling && !unsupported,
                            onClick: () => operate('select', item),
                        },
                        h('span', { class: 'console-avatar' }, initial(item.name)),
                        h(
                            'span',
                            { class: 'console-copy' },
                            h(
                                'span',
                                { class: 'console-row-head' },
                                h(
                                    'span',
                                    { class: 'console-name' },
                                    h('span', { class: 'console-chat-name' }, item.name),
                                    item.pinned === true &&
                                        h(
                                            'span',
                                            {
                                                class: 'console-pin',
                                                title: '已置顶',
                                                role: 'img',
                                                'aria-label': '已置顶',
                                            },
                                            h(
                                                'svg',
                                                { viewBox: '0 0 24 24', 'aria-hidden': true },
                                                h('path', { d: 'M16 9V3H8v6l-3 3v2h14v-2l-3-3ZM12 14v7' })
                                            )
                                        ),
                                    Number(item.unread) > 0 && h('span', { class: 'console-badge' }, item.unread)
                                ),
                                h('span', { class: 'console-time' }, item.time)
                            ),
                            h('span', { class: 'console-preview' }, item.preview)
                        )
                    )
                ),
                !items.length && h(Empty, { icon: '▣' }, unsupported ? '当前平台不支持控制台' : '暂无可用聊天')
            )
        ),
        h(
            'section',
            { class: 'console-chat' },
            h(
                'header',
                { class: 'chat-head' },
                h(
                    Button,
                    { class: 'console-back-btn', 'aria-label': '返回聊天列表', onClick: () => setThread(false) },
                    '‹'
                ),
                h(
                    'div',
                    { class: 'chat-head-title' },
                    h('h3', { class: 'console-chat-title' }, selected.name || '控制台'),
                    h('span', { class: 'console-status' }, status)
                ),
                h(
                    'div',
                    { class: 'chat-head-actions' },
                    h(
                        Button,
                        {
                            class: 'btn-sm console-window-chat-action',
                            variant: 'green',
                            disabled:
                                busy || unsupported || !windowStatus || windowStatus.open || !group || !selected.path,
                            quietDisabled:
                                polling &&
                                !unsupported &&
                                !!windowStatus &&
                                !windowStatus.open &&
                                !!group &&
                                !!selected.path,
                            onClick: () => setConfirming('open'),
                        },
                        '打开独立窗口'
                    ),
                    h(
                        Button,
                        {
                            class: 'btn-sm console-window-chat-action',
                            disabled: busy || unsupported || !windowStatus?.open || !group,
                            quietDisabled: polling && !unsupported && !!windowStatus?.open && !!group,
                            onClick: () => setConfirming('close'),
                        },
                        '关闭独立窗口'
                    ),
                    h(
                        Button,
                        {
                            class: 'btn-sm',
                            busy: phase === 'refresh',
                            disabled: busy || unsupported,
                            quietDisabled: polling && !unsupported,
                            onClick: () => refresh(),
                        },
                        '刷新'
                    )
                )
            ),
            h(Feedback, { error, retry: unsupported ? null : () => refresh() }),
            h(Feedback, { loading: ['select', 'refresh'].includes(phase) }),
            selected.name &&
                !group &&
                !chatlog.loading &&
                h(Feedback, {
                    error: chatlog.error || `未找到「${selected.name}」的聊天记录`,
                    retry: chatlog.reload,
                }),
            h(MessageList, {
                id: 'consoleMessages',
                onReply: (!busy || polling) && !unsupported ? (reply_to_message) => {
                    setMessage((value) => ({ ...emptyMessage(), message: value.message_type === 'text' ? value.message : '', reply_to_message }));
                    requestAnimationFrame(() => document.getElementById('console-composer-text')?.focus());
                } : null,
                resource: messages,
                group: group?.username || '',
                caps: chatlog.data?.health.media,
            }),
            h(MessageComposer, {
                id: 'console-composer',
                value: message,
                onChange: setMessage,
                onSend: () => operate('send'),
                busy: phase === 'send',
                disabled: (busy && phase !== 'poll') || unsupported || !selected.name,
                sendDisabled: busy,
                quietSendDisabled: polling,
                active,
            })
        ),
        manager &&
            h(WindowManager, { busy: !!phase, operate, revision: managerRevision, onClose: () => setManager(false) }),
        confirming &&
            h(
                Modal,
                {
                    title: confirming === 'open' ? '打开独立窗口？' : '关闭独立窗口？',
                    onClose: () => setConfirming(''),
                    footer: [
                        h(Button, { onClick: () => setConfirming('') }, '取消'),
                        h(
                            Button,
                            { variant: confirming === 'open' ? 'green' : 'red', onClick: () => operate(confirming) },
                            '确认'
                        ),
                    ],
                },
                h('p', null, `目标聊天：${selected.name}`),
                h(
                    'p',
                    { class: 'help-text' },
                    confirming === 'open'
                        ? '微信将为这个聊天打开独立窗口。'
                        : '关闭当前聊天的独立窗口，聊天记录不会删除。'
                )
            )
    );
}
