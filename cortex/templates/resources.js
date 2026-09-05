import { h, useRef, useState, useEffect, useResource, useApp, Button, Badge, Modal } from './ui.js';
import { api } from './api.js';
export function useCrud(path, title, active = true) {
    const { toast } = useApp();
    const resource = useResource((signal) => api('GET', path, { signal }), [path], active);
    const [draft, setDraft] = useState(null);
    const [pending, setPending] = useState('');
    const [error, setError] = useState('');
    const [deleting, setDeleting] = useState(null);
    const lock = useRef(false);
    const live = useRef(true);
    useEffect(() => {
        live.current = true;
        return () => {
            live.current = false;
        };
    }, []);
    const alive = useRef(0);
    async function run(key, action) {
        if (lock.current) return;
        lock.current = true;
        setPending(key);
        setError('');
        try {
            await action();
        } catch (e) {
            if (live.current) {
                setError(e.message);
                toast(e.message, 'error');
            }
        } finally {
            lock.current = false;
            setPending('');
        }
    }
    return {
        ...resource,
        reload: () => {
            setError('');
            resource.reload();
        },
        draft,
        pending,
        error: error || resource.error,
        deleting,
        setDeleting,
        change: (values) => setDraft((previous) => ({ ...previous, ...values })),
        create: (defaults) => {
            if (lock.current) return;
            setError('');
            setDraft({ ...defaults, _key: ++alive.current });
        },
        close: () => {
            if (!lock.current) {
                setDraft(null);
                setError('');
            }
        },
        edit: (item) =>
            run(`edit:${item.id}`, async () => {
                const data =
                    path === '/api/webhooks'
                        ? (await api('GET', path)).find((row) => row.id === item.id)
                        : await api('GET', `${path}/${item.id}`);
                if (!data) throw new Error('记录不存在，请刷新列表');
                if (!live.current) return;
                setDraft({ ...data, _key: ++alive.current });
            }),
        save: (payload) =>
            run('save', async () => {
                const data = payload();
                await api(draft.id ? 'PUT' : 'POST', draft.id ? `${path}/${draft.id}` : path, { body: data });
                setDraft(null);
                resource.reload();
                if (live.current) toast(`${title}已保存`, 'success');
            }),
        toggle: (item) =>
            run(`toggle:${item.id}`, async () => {
                await api('PUT', `${path}/${item.id}`, { body: { enabled: !item.enabled } });
                resource.reload();
            }),
        remove: () =>
            run('delete', async () => {
                await api('DELETE', `${path}/${deleting.id}`);
                setDeleting(null);
                resource.reload();
                if (live.current) toast(`${title}已删除`, 'success');
            }),
        test: (item) =>
            run(`test:${item.id}`, async () => {
                await api('POST', `${path}/${item.id}/test`);
                toast('测试请求已发送', 'success');
            }),
    };
}
export function ResourceItem({ item, title, icon, meta, preview, crud, test, kind }) {
    return h(
        'div',
        { class: 'res-item', [`data-${kind}-id`]: item.id },
        icon ? h('div', { class: 'res-icon', 'aria-hidden': true }, icon) : null,
        h(
            'div',
            { class: 'res-body' },
            h(
                'div',
                { class: 'res-title' },
                h('span', { class: 'res-name' }, title),
                h(Badge, { enabled: item.enabled })
            ),
            h(
                'div',
                { class: 'res-meta' },
                meta
                    .filter(Boolean)
                    .map((text, i) => h('span', { key: i }, i > 0 && h('span', { class: 'sep' }, '·'), text))
            ),
            preview && h('div', { class: 'res-preview' }, preview)
        ),
        h(
            'div',
            { class: 'res-actions' },
            h('button', {
                type: 'button',
                class: `toggle ${item.enabled ? 'on' : ''}`,
                role: 'switch',
                'aria-checked': item.enabled,
                'aria-label': `${item.enabled ? '禁用' : '启用'}${title}`,
                disabled: !!crud.pending,
                onClick: () => crud.toggle(item),
            }),
            test && h(Button, { class: 'btn-sm', disabled: !!crud.pending, onClick: () => crud.test(item) }, '测试'),
            h(Button, { class: 'btn-sm', disabled: !!crud.pending, onClick: () => crud.edit(item) }, '编辑'),
            h(
                Button,
                {
                    class: 'btn-sm',
                    variant: 'danger-ghost',
                    disabled: !!crud.pending,
                    onClick: () => crud.setDeleting(item),
                },
                '删除'
            )
        )
    );
}
export function DeleteDialog({ crud, title }) {
    if (!crud.deleting) return null;
    return h(
        Modal,
        {
            title: `删除${title}`,
            onClose: () => crud.setDeleting(null),
            busy: !!crud.pending,
            footer: [
                h(Button, { disabled: !!crud.pending, onClick: () => crud.setDeleting(null) }, '取消'),
                h(Button, { variant: 'red', busy: crud.pending === 'delete', onClick: crud.remove }, '确认删除'),
            ],
        },
        h('p', null, `确定删除「${crud.deleting.name || crud.deleting.key}」吗？`),
        crud.error && h('p', { role: 'alert', class: 'request-error' }, crud.error)
    );
}
export function FormActions({ busy, onClose, editing }) {
    return h(
        'div',
        { class: 'form-actions' },
        h(Button, { type: 'submit', variant: 'green', busy }, editing ? '保存修改' : '创建'),
        h(Button, { disabled: busy, onClick: onClose }, '取消')
    );
}
