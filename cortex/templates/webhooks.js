function toggleReplyJsonPath() {
    const el = $('replyJsonPathGroup');
    if (el) el.style.display = $('webhookReplyMode').value === 'jsonpath' ? '' : 'none';
}

async function testWebhook(id) {
    const r = await api('POST', `/api/webhooks/${id}/test`);
    if (r.ok) toast('测试请求已发送', 'success');
    else toast(r.data?.detail || '测试失败', 'error');
}

const webhookCrud = new CrudManager({
    apiPath: '/api/webhooks', listId: 'webhooksList', formPanelId: 'webhookFormPanel', formId: 'webhookForm',
    formTitleId: 'webhookFormTitle', emptyText: '暂无 Webhook',
    createTitle: '新建 Webhook', editTitle: '编辑 Webhook',
    createMsg: 'Webhook 已创建', updateMsg: 'Webhook 已更新', deleteMsg: 'Webhook 已删除',
    deleteConfirm: '确定要删除这个 Webhook 吗？',

    renderItem: wh => `<div class="crud-item">${itemHeader(wh.name, wh.enabled)}<div class="info-grid">${infoItem('URL', wh.url, 'word-break:break-all')}${infoItem('群聊过滤', wh.group_filter || '全部')}${infoItem('消息过滤', wh.message_filter || '全部')}${infoItem('Body', wh.body_template)}</div>${itemActions('webhookCrud', wh.id, wh.enabled, `<button class="btn btn-ghost btn-sm" onclick="testWebhook(${wh.id})">测试</button>`)}</div>`,

    async fetchItem(id) {
        const r = await api('GET', '/api/webhooks');
        return r.ok ? (r.data.find(w => w.id === id) || null) : null;
    },

    getFormData: () => ({
        name: $('webhookName').value, url: $('webhookUrl').value,
        headers: $('webhookHeaders').value, body_template: $('webhookBodyTemplate').value,
        group_filter: $('webhookGroupFilter').value, message_filter: $('webhookMessageFilter').value,
        enabled: $('webhookEnabled').value === 'true',
        reply_mode: $('webhookReplyMode').value,
        reply_jsonpath: $('webhookReplyJsonPath').value,
    }),

    populateForm: wh => {
        $('webhookName').value = wh.name; $('webhookUrl').value = wh.url;
        $('webhookHeaders').value = wh.headers; $('webhookBodyTemplate').value = wh.body_template;
        $('webhookGroupFilter').value = wh.group_filter || '';
        $('webhookMessageFilter').value = wh.message_filter || '';
        $('webhookEnabled').value = wh.enabled.toString();
        $('webhookReplyMode').value = wh.reply_mode || 'none';
        $('webhookReplyJsonPath').value = wh.reply_jsonpath || '';
        toggleReplyJsonPath();
    },

    resetForm: () => {
        $('webhookHeaders').value = '{}'; $('webhookBodyTemplate').value = '{}';
        $('webhookGroupFilter').value = ''; $('webhookMessageFilter').value = '';
        $('webhookReplyMode').value = 'none'; $('webhookReplyJsonPath').value = '';
        toggleReplyJsonPath();
    },
});
