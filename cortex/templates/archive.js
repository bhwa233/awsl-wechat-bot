import {
    h, useState, useRef, useResource, usePoll, useApp,
    Button, Badge, Empty, Feedback, Field, Input, Select, PageHead, Modal,
} from './ui.js';
import { api } from './api.js';

const dateTime = (value) => value ? new Date(value).toLocaleString('zh-CN') : '—';

export function Archive({ active }) {
    const { toast } = useApp();
    const status = useResource((signal) => api('GET', '/api/archive/status', { signal }), [], active);
    const enabled = status.data?.enabled;
    const available = useResource((signal) => api('GET', '/api/archive/available-groups', { signal }), [], active && enabled);
    const [filter, setFilter] = useState('');
    const [selected, setSelected] = useState('');
    const [pending, setPending] = useState(false);
    const [error, setError] = useState('');
    const [replay, setReplay] = useState(null);
    const lock = useRef(false);
    const groups = status.data?.groups || [];
    const choices = (available.data || []).filter((group) =>
        !groups.some((saved) => saved.talker === group.username) &&
        `${group.display_name} ${group.username}`.toLowerCase().includes(filter.toLowerCase())
    );
    const choice = choices.some((group) => group.username === selected) ? selected : choices[0]?.username || '';
    usePoll(status.reload, active && enabled, 10000);

    async function change(path, body) {
        if (lock.current) return;
        lock.current = true;
        setPending(true);
        setError('');
        try {
            await api(path === 'groups' ? 'PUT' : 'POST', `/api/archive/${path}`, { body });
            setReplay(null);
            status.reload();
            available.reload();
            toast(path === 'replay' ? '已安排重新补录' : '归档设置已保存', 'success');
        } catch (e) {
            setError(e.message);
        } finally {
            lock.current = false;
            setPending(false);
        }
    }
    return h('div', null,
        h(PageHead, { title: '云端归档', subtitle: '指定群聊历史补录与持续同步，云端保存各类消息的内容及元数据。' },
            h(Button, { busy: status.loading, onClick: () => { status.reload(); available.reload(); } }, '刷新状态')),
        h(Feedback, { error: status.error || error, retry: status.reload }),
        !status.data && h(Feedback, { loading: true }),
        status.data && !enabled && h('div', { class: 'card' },
            h('h3', null, '归档尚未就绪'), h('p', { class: 'help-text' }, status.data.reason)),
        enabled && h('div', null,
            h('div', { class: 'card archive-overview' },
                h('h3', null, status.data.running ? '同步进程运行中' : '未检测到同步进程'),
                h('p', { class: 'help-text' }, `账号：${status.data.account_id} · 设备：${status.data.source_id}`),
                h('p', null, `待上传 ${status.data.pending} 条 · ${(status.data.pending_bytes / 1024 / 1024).toFixed(1)} MB`),
                h('p', { class: 'help-text' }, `最近心跳：${dateTime(status.data.heartbeat)}`),
                status.data.last_error && h(Feedback, { error: status.data.last_error })),
            h('div', { class: 'card archive-add' },
                h('h3', null, '添加同步群聊'),
                h('p', { class: 'help-text' }, '显示本机可读取且云端凭证已授权的群聊。添加后自动补录已有历史。'),
                h(Feedback, { loading: available.loading, error: available.error, retry: available.reload }),
                h('form', { onSubmit: (e) => { e.preventDefault(); if (choice) change('groups', { talker: choice, enabled: true }); } },
                    h('div', { class: 'form-row' },
                        h(Field, { label: '筛选群聊' }, h(Input, { value: filter, onValue: setFilter, placeholder: '群名或群 ID' })),
                        h(Field, { label: '同步群聊' }, h(Select, {
                            value: choice, onValue: setSelected, disabled: available.loading || !!available.error || !choices.length,
                            options: choices.length ? choices.map((group) => [group.username, `${group.display_name} · ${group.username}`])
                                : [['', '暂无可添加的已授权群聊']],
                        }))),
                    h(Button, { type: 'submit', variant: 'green', busy: pending,
                        disabled: !choice || available.loading || !!available.error || status.loading }, '添加并同步'))),
            h('div', { class: 'resource-list', id: 'archiveGroups' },
                !groups.length && h(Empty, null, '尚未选择同步群聊'),
                groups.map((group) => h('div', { class: 'res-item', key: group.talker },
                    h('div', { class: 'res-body' },
                        h('div', { class: 'res-title' }, h('span', { class: 'res-name' }, group.display_name),
                            h(Badge, { enabled: group.enabled }, group.enabled ? '同步已启用' : '已暂停')),
                        h('div', { class: 'res-preview' }, group.talker),
                        h('div', { class: 'res-meta' },
                            `${group.history_done ? '历史补录完成' : '历史补录中'} · 待上传 ${group.pending} 条`),
                        h('p', { class: 'help-text' }, `最近读取：${dateTime(group.last_read)} · 最近上传：${dateTime(group.last_upload)}`),
                        group.last_error && h(Feedback, { error: group.last_error })),
                    h('div', { class: 'res-actions' },
                        h(Button, { disabled: pending || status.loading, onClick: () => change('groups', {
                            talker: group.talker, enabled: !group.enabled,
                        }) }, group.enabled ? '暂停' : '继续同步'),
                        h(Button, { disabled: pending || status.loading, onClick: () => setReplay(group) }, '重新补录'))))),
            h(ArchiveQuery, { groups, active })),
        replay && h(Modal, {
            title: '重新补录历史', busy: pending, onClose: () => setReplay(null),
            footer: [h(Button, { disabled: pending, onClick: () => setReplay(null) }, '取消'),
                h(Button, { variant: 'green', busy: pending, onClick: () => change('replay', { talker: replay.talker }) }, '确认重新补录')],
        }, h('p', null, `重新读取「${replay.display_name}」本机现存的全部历史并上传。云端按消息身份去重，已有队列会保留。`),
        h('p', { class: 'help-text' }, '已暂停的群需继续同步后才会补录。'), h(Feedback, { error }))
    );
}

function ArchiveQuery({ groups, active }) {
    const [talker, setTalker] = useState('');
    const [keyword, setKeyword] = useState('');
    const [sender, setSender] = useState('');
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');
    const [query, setQuery] = useState(null);
    const selected = groups.some((group) => group.talker === talker) ? talker : groups[0]?.talker || '';
    const queryKey = JSON.stringify(query);
    const result = useResource(async (signal) => ({
        ...await api('GET', `/api/archive/messages?${new URLSearchParams(query)}`, { signal }), query_key: queryKey,
    }), [queryKey], active && !!query);
    const [validation, setValidation] = useState('');
    function search(event) {
        event.preventDefault();
        if (!selected) return;
        if (start && end && new Date(start) > new Date(end)) {
            setValidation('开始时间不得晚于结束时间');
            return;
        }
        setValidation('');
        const next = { talker: selected, limit: '20' };
        if (keyword) next.q = keyword;
        if (sender) next.sender = sender;
        if (start) next.start = String(Math.floor(new Date(start).getTime() / 1000));
        if (end) next.end = String(Math.floor(new Date(end).getTime() / 1000));
        setQuery(next);
        result.reload();
    }
    return h('div', { class: 'card archive-query' },
        h('h3', null, '查询云端记录'),
        h('form', { onSubmit: search },
            h('div', { class: 'form-row' },
                h(Field, { label: '查询群聊' }, h(Select, { value: selected, onValue: setTalker,
                    options: groups.length ? groups.map((group) => [group.talker, `${group.display_name} · ${group.talker}`]) : [['', '请先添加同步群聊']] })),
                h(Field, { label: '正文关键词' }, h(Input, { value: keyword, onValue: setKeyword, maxLength: 256, placeholder: '留空查询全部消息类型' }))),
            h('div', { class: 'form-row' },
                h(Field, { label: '开始时间' }, h(Input, { type: 'datetime-local', value: start, onValue: setStart })),
                h(Field, { label: '结束时间' }, h(Input, { type: 'datetime-local', value: end, onValue: setEnd })),
                h(Field, { label: '发送人 ID' }, h(Input, { value: sender, onValue: setSender, maxLength: 200, placeholder: '可选，填写微信 ID' }))),
            h(Button, { type: 'submit', variant: 'green', disabled: !selected, busy: !!query && result.loading }, '查询云端')),
        h(Feedback, { error: validation || (query && result.error), loading: !!query && result.loading, retry: result.reload }),
        query && !result.loading && !result.error && result.data?.query_key === queryKey && h('div', { class: 'archive-results' },
            h('p', { class: 'help-text' }, `结果群聊：${groups.find((group) => group.talker === query.talker)?.display_name || query.talker} · ${query.talker}`),
            !result.data.messages.length && h(Empty, null, '没有匹配的云端记录'),
            result.data.messages.map((message) => h('article', { class: 'res-item archive-message', key: message.id },
                h('div', { class: 'res-body' },
                    h('div', { class: 'res-title' }, `${message.sender_name || message.sender || '未知发送人'}${message.is_self ? '（自己）' : ''}`),
                    h('p', { class: 'help-text' }, `${dateTime(message.create_time * 1000)} · ${message.sender || '发送人未知'} · 类型 ${message.msg_type}`),
                    h('p', { class: 'archive-text' }, message.text || '此消息暂无可检索正文'),
                    h('details', null, h('summary', null, '查看原内容与消息 ID'),
                        h('p', { class: 'help-text' }, `消息 ID：${message.server_id}`), h('pre', { class: 'archive-text' }, message.content))))),
            h('div', { class: 'form-actions' },
                query.cursor && h(Button, { onClick: () => { const { cursor, ...first } = query; setQuery(first); } }, '返回最新'),
                result.data.next_cursor && h(Button, { onClick: () => setQuery({ ...query, cursor: result.data.next_cursor }) }, '更早记录')))
    );
}
