import {
    h,
    useRef,
    useState,
    useResource,
    useApp,
    Button,
    Badge,
    Empty,
    Feedback,
    Field,
    Input,
    Select,
    EnabledField,
    GroupPicker,
    PageHead,
    Modal,
} from './ui.js';
import { api, groupName } from './api.js';
import { useCrud, ResourceItem, DeleteDialog, FormActions } from './resources.js';
import { MessageComposer, MessagePreview, emptyMessage, messagePayload } from './message_composer.js';

function CommandForm({ crud }) {
    const { groups, capabilities } = useApp();
    const draft = crud.draft;
    const [message, setMessage] = useState(() => ({
        message_type: draft.action_type || draft.message_type || 'text',
        message: draft.message || '',
        filename: draft.filename || '',
    }));
    function payload() {
        const key = draft.key.trim();
        if (!key || /\s/.test(key)) throw new Error('命令 Key 不能为空或包含空白字符');
        const payload = messagePayload(message, capabilities);
        return {
            key,
            description: draft.description.trim(),
            action_type: payload.message_type,
            message: payload.message,
            filename: payload.filename,
            target_groups: draft.target_groups || [],
            enabled: draft.enabled,
        };
    }
    return h(
        Modal,
        {
            title: draft.id ? '编辑自定义命令' : '新建自定义命令',
            onClose: crud.close,
            busy: !!crud.pending,
            class: 'modal-lg',
            id: 'customCommandFormHost',
        },
        h(
            'form',
            {
                id: 'customCommandForm',
                onSubmit: (e) => {
                    e.preventDefault();
                    crud.save(payload);
                },
            },
            h(
                'fieldset',
                { class: 'form-fields', disabled: !!crud.pending },
                h(
                    'div',
                    { class: 'form-row' },
                    h(
                        Field,
                        { label: '命令 Key' },
                        h(Input, {
                            value: draft.key,
                            required: true,
                            placeholder: 'menu',
                            onValue: (key) => crud.change({ key }),
                        })
                    ),
                    h(EnabledField, { value: draft.enabled, onValue: (enabled) => crud.change({ enabled }) })
                ),
                h(
                    Field,
                    { label: '描述' },
                    h(Input, {
                        value: draft.description || '',
                        placeholder: 'menu - 查看菜单',
                        onValue: (description) => crud.change({ description }),
                    })
                ),
                h(MessageComposer, {
                    value: message,
                    onChange: setMessage,
                    summary: true,
                    busy: !!crud.pending,
                    id: 'command-composer',
                }),
                h(
                    'div',
                    { class: 'form-group' },
                    h('span', { class: 'field-label' }, '目标群组（空 = 全部）'),
                    h(GroupPicker, {
                        names: groups.map(groupName),
                        value: draft.target_groups,
                        onValue: (target_groups) => crud.change({ target_groups }),
                    })
                )
            ),
            h(Feedback, { error: crud.error }),
            h(FormActions, { busy: !!crud.pending, editing: !!draft.id, onClose: crud.close })
        )
    );
}
export function CustomCommands({ active }) {
    const crud = useCrud('/api/custom-commands', '自定义命令', active);
    const { groups } = useApp();
    const [filter, setFilter] = useState('__all__');
    const items = crud.data || [];
    const names = [...new Set([...groups.map(groupName), ...items.flatMap((item) => item.target_groups || [])])].sort(
        (a, b) => a.localeCompare(b, 'zh-CN')
    );
    const shown = items.filter(
        (item) =>
            filter === '__all__' ||
            !item.target_groups?.length ||
            (filter !== '__global__' && item.target_groups.includes(filter))
    );
    return h(
        'div',
        null,
        h(
            PageHead,
            { title: '自定义命令' },
            h(Button, { onClick: crud.reload }, '刷新'),
            h(
                Button,
                {
                    variant: 'green',
                    disabled: !!crud.pending,
                    onClick: () =>
                        crud.create({
                            key: '',
                            description: '',
                            enabled: true,
                            target_groups: [],
                            action_type: 'text',
                            ...emptyMessage(),
                        }),
                },
                '新建命令'
            )
        ),
        h(
            'div',
            { class: 'command-filter-bar' },
            h(
                'div',
                { class: 'command-filter-copy' },
                h('span', { class: 'command-filter-label' }, '适用群'),
                h('span', { class: 'command-filter-hint' }, '指定群同时显示全局命令')
            ),
            h(Select, {
                id: 'customCommandGroupFilter',
                class: 'command-filter-select',
                'aria-label': '按适用群筛选',
                value: filter,
                onValue: setFilter,
                options: [['__all__', '全部命令'], ['__global__', '仅全局命令'], ...names.map((name) => [name, name])],
            }),
            h('span', { class: 'command-filter-count' }, `${shown.length} / ${items.length}`)
        ),
        h(Feedback, { ...crud, retry: crud.reload }),
        h(
            'div',
            { id: 'customCommandsList' },
            shown.map((item) =>
                h(ResourceItem, {
                    key: item.id,
                    kind: 'command',
                    item,
                    crud,
                    title: item.key,
                    meta: [
                        item.description,
                        item.action_type || item.message_type,
                        item.target_groups?.length ? `${item.target_groups.length} 个群` : '所有群',
                    ],
                    preview: h(MessagePreview, {
                        type: item.action_type || item.message_type,
                        message: item.message,
                        filename: item.filename,
                    }),
                })
            )
        ),
        !crud.loading &&
            !crud.error &&
            !shown.length &&
            h(Empty, { icon: null }, items.length ? '这个群暂无适用命令' : '还没有自定义命令'),
        crud.draft && h(CommandForm, { key: crud.draft._key, crud }),
        h(DeleteDialog, { crud, title: '自定义命令' })
    );
}

export function RemoteCommands({ active }) {
    const { groups, toast } = useApp();
    const resource = useResource((signal) => api('GET', '/api/remote-commands/policy', { signal }), [], active);
    const [draft, setDraft] = useState(null);
    const [busy, setBusy] = useState('');
    const [error, setError] = useState('');
    const lock = useRef(false);
    const current = resource.data;
    const allowlist = draft?.allowlist || current?.allowlist || [];
    const commands = current?.available_commands || [];
    const bypass = draft?.bypass_groups || current?.bypass_groups || [];
    async function change(action) {
        if (lock.current) return;
        lock.current = true;
        setBusy(action);
        setError('');
        try {
            const next =
                action === 'save'
                    ? await api('PUT', '/api/remote-commands/policy', { body: draft })
                    : await api('POST', '/api/remote-commands/refresh');
            resource.update(next);
            setDraft(null);
            toast(action === 'save' ? '远程命令策略已保存' : '远程命令列表已刷新', 'success');
        } catch (e) {
            setError(e.message);
        } finally {
            lock.current = false;
            setBusy('');
        }
    }
    function toggleKey(key, checked) {
        const next = draft.allowlist.filter((value) => value.toLowerCase() !== key.toLowerCase());
        setDraft({ ...draft, allowlist: checked ? [...next, key] : next });
    }
    return h(
        'div',
        null,
        h(
            PageHead,
            { title: '远程命令' },
            h(
                Button,
                { disabled: !current || !!draft || !!busy, busy: busy === 'refresh', onClick: () => change('refresh') },
                '刷新命令列表'
            ),
            h(
                Button,
                {
                    disabled: !!busy || (!draft && (!current || resource.loading || !!resource.error)),
                    onClick: () =>
                        setDraft(
                            draft
                                ? null
                                : {
                                      allowlist: [...(current.allowlist || [])],
                                      bypass_groups: [...(current.bypass_groups || [])],
                                  }
                        ),
                },
                draft ? '取消' : '编辑'
            ),
            draft && h(Button, { variant: 'green', busy: !!busy, onClick: () => change('save') }, '保存')
        ),
        h(Feedback, { loading: resource.loading, error: error || resource.error, retry: resource.reload }),
        current &&
            h(
                'div',
                { class: 'card' },
                h(
                    'div',
                    { class: 'remote-policy-stats' },
                    [
                        `远程命令 ${commands.length}`,
                        `白名单 ${allowlist.length || '全部'}`,
                        `豁免群 ${bypass.length}`,
                    ].map((text) => h('span', { class: 'policy-stat', key: text }, text))
                ),
                h(
                    'div',
                    { class: 'remote-section' },
                    h('div', { class: 'remote-section-label' }, '命令白名单'),
                    h(
                        'div',
                        { class: 'remote-section-value' },
                        h(
                            'div',
                            { class: 'remote-command-list' },
                            commands.map((command) => {
                                const checked = allowlist.some(
                                    (key) => key.toLowerCase() === command.key.toLowerCase()
                                );
                                return draft
                                    ? h(
                                          'label',
                                          { class: 'checkbox-item', key: command.key },
                                          h('input', {
                                              type: 'checkbox',
                                              checked,
                                              disabled: !!busy,
                                              onChange: (e) => toggleKey(command.key, e.currentTarget.checked),
                                          }),
                                          h(
                                              'span',
                                              null,
                                              command.key,
                                              h('span', { class: 'checkbox-sub' }, command.description)
                                          )
                                      )
                                    : h(
                                          'div',
                                          { class: 'remote-cmd-row', key: command.key },
                                          h('span', { class: 'remote-cmd-key' }, command.key),
                                          h('span', { class: 'remote-cmd-desc' }, command.description),
                                          h(
                                              Badge,
                                              { enabled: !allowlist.length || checked },
                                              !allowlist.length ? '允许' : checked ? '白名单' : '未白名单'
                                          )
                                      );
                            })
                        ),
                        !commands.length && h('div', { class: 'empty-hint' }, '未加载到远程命令'),
                        h('div', { class: 'help-text' }, '不勾选任何命令 = 允许全部远程命令。')
                    )
                ),
                h(
                    'div',
                    { class: 'remote-section' },
                    h('div', { class: 'remote-section-label' }, '豁免群组'),
                    h(
                        'div',
                        { class: 'remote-section-value' },
                        draft
                            ? h(
                                  'fieldset',
                                  { disabled: !!busy, class: 'form-fields' },
                                  h(GroupPicker, {
                                      names: groups.map(groupName),
                                      value: bypass,
                                      onValue: (bypass_groups) => setDraft({ ...draft, bypass_groups }),
                                  })
                              )
                            : bypass.length
                              ? bypass.map((name) => h('span', { class: 'policy-chip', key: name }, name))
                              : '无'
                    )
                )
            )
    );
}
