import {
    h,
    useState,
    useResource,
    useApp,
    Button,
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
import { api, groupName, formatTime } from './api.js';
import { useCrud, ResourceItem, DeleteDialog, FormActions } from './resources.js';
import { MessageComposer, MessagePreview, emptyMessage, messagePayload } from './message_composer.js';

function TaskForm({ crud }) {
    const { groups, capabilities } = useApp();
    const draft = crud.draft;
    const [message, setMessage] = useState(() => ({
        message_type: draft.task_type === 'summary' ? 'summary' : draft.message_type || 'text',
        message: draft.task_type === 'summary' ? '' : draft.message || '',
        filename: draft.filename || '',
    }));
    const [summaryRows, setSummaryRows] = useState(() => {
        try {
            const rows = JSON.parse(draft.task_type === 'summary' ? draft.message : '{}').groups;
            if (!Array.isArray(rows)) return [];
            return rows
                .filter((row) => row && typeof row === 'object' && !Array.isArray(row))
                .map((row) => ({
                    group_id: typeof row.group_id === 'string' ? row.group_id : '',
                    group_name: typeof row.group_name === 'string' ? row.group_name : '',
                }));
        } catch {
            return [];
        }
    });
    const sources = useResource(
        (signal) => api('GET', '/api/chatlog/groups', { signal }),
        [],
        message.message_type === 'summary'
    );
    const names = [...new Set(groups.map(groupName))];
    const sourceNames = new Map((sources.data || []).map((g) => [g.username, groupName(g)]));
    const sourceOptions = [
        ['', '来源会话…'],
        ...(sources.data || []).map((g) => [g.username, groupName(g)]),
        ...summaryRows
            .filter((row) => row.group_id && !sourceNames.has(row.group_id))
            .map((row) => [row.group_id, row.group_id]),
    ];
    function setRow(index, values) {
        setSummaryRows((rows) => rows.map((row, i) => (i === index ? { ...row, ...values } : row)));
    }
    function payload() {
        const data = {
            name: draft.name.trim(),
            cron_expression: draft.cron_expression.trim(),
            enabled: draft.enabled,
            task_type: message.message_type === 'summary' ? 'summary' : 'send',
            target_groups: draft.target_groups || [],
        };
        if (!data.name) throw new Error('请输入任务名称');
        if (data.task_type === 'summary') {
            if (!summaryRows.length || summaryRows.some((row) => !row.group_id || !row.group_name))
                throw new Error('请完整填写总结的来源会话和发送目标');
            return {
                ...data,
                message: JSON.stringify({ groups: summaryRows }),
                message_type: 'text',
                filename: '',
                target_groups: [],
            };
        }
        return { ...data, ...messagePayload(message, capabilities) };
    }
    return h(
        Modal,
        {
            title: draft.id ? '编辑定时任务' : '新建定时任务',
            onClose: crud.close,
            busy: !!crud.pending,
            class: 'modal-lg',
            id: 'taskFormHost',
        },
        h(
            'form',
            {
                id: 'taskForm',
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
                        { label: '任务名称' },
                        h(Input, { value: draft.name, required: true, onValue: (name) => crud.change({ name }) })
                    ),
                    h(EnabledField, { value: draft.enabled, onValue: (enabled) => crud.change({ enabled }) })
                ),
                h(
                    Field,
                    {
                        label: 'Cron 表达式',
                        help: '分 时 日 月 周 · 0 9 * * * = 每天 9 点 · */30 * * * * = 每 30 分钟',
                    },
                    h(Input, {
                        value: draft.cron_expression,
                        required: true,
                        maxLength: 256,
                        placeholder: '0 9 * * *',
                        onValue: (cron_expression) => crud.change({ cron_expression }),
                    })
                ),
                h(MessageComposer, {
                    value: message,
                    onChange: setMessage,
                    summary: true,
                    busy: !!crud.pending,
                    id: 'task-composer',
                }),
                message.message_type === 'summary'
                    ? h(
                          'div',
                          { class: 'form-group' },
                          h('span', { class: 'field-label' }, '总结配置'),
                          h(Feedback, { ...sources, retry: sources.reload }),
                          summaryRows.map((row, index) =>
                              h(
                                  'div',
                                  { class: 'summary-row', key: index },
                                  h(Select, {
                                      'aria-label': '来源会话',
                                      value: row.group_id,
                                      options: sourceOptions,
                                      onValue: (group_id) => {
                                          const name = sourceNames.get(group_id);
                                          setRow(index, {
                                              group_id,
                                              ...(names.includes(name) ? { group_name: name } : {}),
                                          });
                                      },
                                  }),
                                  h(Select, {
                                      'aria-label': '发送目标',
                                      value: row.group_name,
                                      options: [
                                          ['', '发送到…'],
                                          ...[...new Set([...names, row.group_name].filter(Boolean))].map((name) => [
                                              name,
                                              name,
                                          ]),
                                      ],
                                      onValue: (group_name) => setRow(index, { group_name }),
                                  }),
                                  h(
                                      Button,
                                      {
                                          'aria-label': '移除总结会话',
                                          onClick: () => setSummaryRows((rows) => rows.filter((_, i) => i !== index)),
                                      },
                                      '×'
                                  )
                              )
                          ),
                          h(
                              Button,
                              {
                                  class: 'btn-sm',
                                  onClick: () => setSummaryRows((rows) => [...rows, { group_id: '', group_name: '' }]),
                              },
                              '+ 添加群聊'
                          )
                      )
                    : h(
                          'div',
                          { class: 'form-group' },
                          h('span', { class: 'field-label' }, '目标群组（空 = 全部）'),
                          h(GroupPicker, {
                              names,
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
export function Tasks({ active }) {
    const crud = useCrud('/api/tasks', '定时任务', active);
    const { health } = useApp();
    const zone = health?.timezone?.includes('?') ? `UTC${health.timezone_offset || ''}` : health?.timezone || '';
    return h(
        'div',
        null,
        h(
            PageHead,
            {
                title: '定时任务',
                subtitle: zone ? `服务器时区 ${zone} · ${formatTime(health.server_time)}` : '',
            },
            h(Button, { onClick: crud.reload }, '刷新'),
            h(
                Button,
                {
                    variant: 'green',
                    disabled: !!crud.pending,
                    onClick: () =>
                        crud.create({
                            name: '',
                            cron_expression: '',
                            enabled: true,
                            target_groups: [],
                            task_type: 'send',
                            ...emptyMessage(),
                        }),
                },
                '新建任务'
            )
        ),
        h(Feedback, { ...crud, retry: crud.reload }),
        h(
            'div',
            { id: 'tasksList' },
            (crud.data || []).map((item) =>
                h(ResourceItem, {
                    key: item.id,
                    kind: 'task',
                    item,
                    crud,
                    title: item.name,
                    icon: item.task_type === 'summary' ? '▤' : '◷',
                    meta: [
                        item.cron_expression,
                        item.task_type === 'summary' ? '群聊总结' : item.message_type,
                        item.target_groups?.length ? `${item.target_groups.length} 个群` : '所有群',
                        `最后运行 ${item.last_run ? formatTime(item.last_run) : '从未'}`,
                    ],
                    preview: h(MessagePreview, {
                        type: item.task_type === 'summary' ? 'summary' : item.message_type,
                        message: item.message,
                        filename: item.filename,
                    }),
                })
            )
        ),
        !crud.loading && !crud.error && !crud.data?.length && h(Empty, { icon: '◷' }, '暂无定时任务'),
        crud.draft && h(TaskForm, { key: crud.draft._key, crud }),
        h(DeleteDialog, { crud, title: '定时任务' })
    );
}
