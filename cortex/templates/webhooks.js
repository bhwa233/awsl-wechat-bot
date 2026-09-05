import { h, Button, Empty, Feedback, Field, Input, Select, EnabledField, PageHead, Modal } from './ui.js';
import { useCrud, ResourceItem, DeleteDialog, FormActions } from './resources.js';

const defaults = {
    name: '',
    enabled: true,
    url: '',
    group_filter: '',
    message_filter: '',
    headers: '{}',
    body_template: '{}',
    reply_mode: 'none',
    reply_jsonpath: '',
};
export function Webhooks({ active }) {
    const crud = useCrud('/api/webhooks', 'Webhook', active);
    const { draft } = crud;
    const input = (key, label, props = {}) =>
        h(
            Field,
            { label },
            h(Input, { ...props, value: draft[key] || '', onValue: (value) => crud.change({ [key]: value }) })
        );
    const textarea = (key, label) =>
        h(
            Field,
            { label },
            h('textarea', { value: draft[key], rows: 3, onInput: (e) => crud.change({ [key]: e.currentTarget.value }) })
        );
    function payload() {
        for (const key of ['headers', 'body_template']) {
            try {
                JSON.parse(draft[key]);
            } catch {
                throw new Error(`${key === 'headers' ? 'Headers' : 'Body 模板'} 必须是有效 JSON`);
            }
        }
        return Object.fromEntries(
            Object.keys(defaults).map((key) => [
                key,
                ['name', 'url', 'reply_jsonpath'].includes(key) ? draft[key].trim() : draft[key],
            ])
        );
    }
    return h(
        'div',
        null,
        h(
            PageHead,
            { title: 'Webhook' },
            h(Button, { onClick: crud.reload }, '刷新'),
            h(
                Button,
                { variant: 'green', disabled: !!crud.pending, onClick: () => crud.create(defaults) },
                '新建 Webhook'
            )
        ),
        h(Feedback, { ...crud, retry: crud.reload }),
        h(
            'div',
            { id: 'webhooksList' },
            (crud.data || []).map((item) =>
                h(ResourceItem, {
                    key: item.id,
                    item,
                    crud,
                    kind: 'webhook',
                    test: true,
                    title: item.name,
                    icon: '↗',
                    meta: [
                        item.url,
                        item.reply_mode === 'jsonpath'
                            ? 'JSONPath 回复'
                            : item.reply_mode === 'text'
                              ? '文本回复'
                              : '不回复',
                        item.group_filter && `群：${item.group_filter}`,
                        item.message_filter && `消息：${item.message_filter}`,
                    ],
                })
            )
        ),
        !crud.loading && !crud.error && !crud.data?.length && h(Empty, { icon: '↗' }, '暂无 Webhook'),
        draft &&
            h(
                Modal,
                {
                    title: draft.id ? '编辑 Webhook' : '新建 Webhook',
                    onClose: crud.close,
                    busy: !!crud.pending,
                    class: 'modal-lg',
                    id: 'webhookFormHost',
                },
                h(
                    'form',
                    {
                        id: 'webhookForm',
                        onSubmit: (e) => {
                            e.preventDefault();
                            crud.save(payload);
                        },
                    },
                    h(
                        'fieldset',
                        { disabled: !!crud.pending, class: 'form-fields' },
                        h(
                            'div',
                            { class: 'form-row' },
                            input('name', '名称', { required: true }),
                            h(EnabledField, { value: draft.enabled, onValue: (enabled) => crud.change({ enabled }) })
                        ),
                        input('url', 'URL', { type: 'url', required: true }),
                        h(
                            'div',
                            { class: 'form-row' },
                            input('group_filter', '群聊过滤（正则）', { placeholder: '空 = 全部' }),
                            input('message_filter', '消息过滤（正则）', { placeholder: '空 = 全部' })
                        ),
                        textarea('headers', 'Headers（JSON）'),
                        textarea('body_template', 'Body 模板（JSON）'),
                        h('p', { class: 'help-text' }, '占位符：{{group_name}} 群名 · {{message}} 消息原文'),
                        h(
                            Field,
                            { label: '回复模式' },
                            h(Select, {
                                value: draft.reply_mode,
                                onValue: (reply_mode) => crud.change({ reply_mode }),
                                options: [
                                    ['none', '不回复'],
                                    ['text', '纯文本'],
                                    ['jsonpath', 'JSONPath'],
                                ],
                            })
                        ),
                        draft.reply_mode === 'jsonpath' &&
                            input('reply_jsonpath', 'JSONPath 表达式', { placeholder: '$.data.reply' })
                    ),
                    h(Feedback, { error: crud.error }),
                    h(FormActions, { busy: !!crud.pending, editing: !!draft.id, onClose: crud.close })
                )
            ),
        h(DeleteDialog, { crud, title: 'Webhook' })
    );
}
