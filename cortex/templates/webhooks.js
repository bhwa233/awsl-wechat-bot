// ============================================================================
// webhooks.js — Webhook 管理模块
// ============================================================================

function toggleReplyJsonPath() {
    const el = $('replyJsonPathGroup');
    if (el) el.style.display = $('webhookReplyMode').value === 'jsonpath' ? '' : 'none';
}

async function testWebhook(id) {
    const r = await api('POST', `/api/webhooks/${id}/test`);
    if (r.ok) toast('测试请求已发送', 'success');
    else toast(r.data?.detail || '测试失败', 'error');
}

const webhookCrud = {
    apiPath: '/api/webhooks',
    items: [],
    editingId: null,

    renderItem(wh) {
        const replyLabel = wh.reply_mode === 'jsonpath' ? 'JSONPath 回复' : wh.reply_mode === 'text' ? '文本回复' : '不回复';
        const filters = [];
        if (wh.group_filter) filters.push(`群: ${wh.group_filter}`);
        if (wh.message_filter) filters.push(`消息: ${wh.message_filter}`);
        return `<div class="res-item" data-webhook-id="${wh.id}">
            <div class="res-icon">🔗</div>
            <div class="res-body">
                <div class="res-title"><span class="res-name">${escapeHtml(wh.name)}</span><span class="badge ${wh.enabled ? 'on' : 'off'}">${wh.enabled ? '已启用' : '已禁用'}</span></div>
                <div class="res-meta">
                    <span class="mono">${escapeHtml(wh.url)}</span><span class="sep">·</span>
                    <span>${replyLabel}</span>
                    ${filters.length ? `<span class="sep">·</span><span>${escapeHtml(filters.join(' · '))}</span>` : ''}
                </div>
            </div>
            <div class="res-actions">
                <button class="toggle${wh.enabled ? ' on' : ''}" onclick="webhookCrud.toggle(${wh.id}, ${wh.enabled})" title="${wh.enabled ? '禁用' : '启用'}"></button>
                <button class="btn btn-ghost btn-sm" onclick="testWebhook(${wh.id})">测试</button>
                <button class="btn btn-ghost btn-sm" onclick="webhookCrud.edit(${wh.id})">编辑</button>
                <button class="btn btn-danger-ghost btn-sm" onclick="webhookCrud.remove(${wh.id})">删除</button>
            </div>
        </div>`;
    },

    render() {
        const el = $('webhooksList');
        if (!this.items.length) el.innerHTML = '<div class="empty-hint">暂无 Webhook</div>';
        else el.innerHTML = this.items.map(item => this.renderItem(item)).join('');
    },

    async load() {
        $('webhooksList').innerHTML = '<div class="loading-hint">加载中…</div>';
        const r = await api('GET', this.apiPath);
        if (!r.ok) { toast(r.data?.detail || r.error || '加载 Webhook 失败', 'error'); return; }
        this.items = Array.isArray(r.data) ? r.data : [];
        this.render();
    },

    async fetchItem(id) {
        const r = await api('GET', this.apiPath);
        return r.ok ? (r.data.find(w => w.id === id) || null) : null;
    },

    getFormData() {
        const name = $('webhookName').value.trim();
        const url = $('webhookUrl').value.trim();
        if (!name) { toast('请输入名称', 'error'); return null; }
        if (!url) { toast('请输入 URL', 'error'); return null; }
        const headers = $('webhookHeaders').value.trim() || '{}';
        const bodyTemplate = $('webhookBodyTemplate').value.trim() || '{}';
        try { JSON.parse(headers); } catch { toast('Headers 不是合法 JSON', 'error'); return null; }
        try { JSON.parse(bodyTemplate); } catch { toast('Body 模板不是合法 JSON', 'error'); return null; }
        return {
            name, url,
            headers, body_template: bodyTemplate,
            group_filter: $('webhookGroupFilter').value, message_filter: $('webhookMessageFilter').value,
            enabled: $('webhookEnabled').value === 'true',
            reply_mode: $('webhookReplyMode').value,
            reply_jsonpath: $('webhookReplyJsonPath').value,
        };
    },

    populateForm(wh) {
        $('webhookName').value = wh.name; $('webhookUrl').value = wh.url;
        $('webhookHeaders').value = wh.headers; $('webhookBodyTemplate').value = wh.body_template;
        $('webhookGroupFilter').value = wh.group_filter || '';
        $('webhookMessageFilter').value = wh.message_filter || '';
        $('webhookEnabled').value = wh.enabled.toString();
        $('webhookReplyMode').value = wh.reply_mode || 'none';
        $('webhookReplyJsonPath').value = wh.reply_jsonpath || '';
        toggleReplyJsonPath();
    },

    resetForm() {
        $('webhookHeaders').value = '{}'; $('webhookBodyTemplate').value = '{}';
        $('webhookGroupFilter').value = ''; $('webhookMessageFilter').value = '';
        $('webhookReplyMode').value = 'none'; $('webhookReplyJsonPath').value = '';
        toggleReplyJsonPath();
    },

    showForm() {
        this.editingId = null;
        $('webhookFormTitle').textContent = '新建 Webhook';
        $('webhookFormBtn').textContent = '创建 Webhook';
        $('webhookForm').reset();
        this.resetForm();
        $('webhookFormHost').style.display = 'flex';
        $('webhookName').focus();
    },

    hideForm() {
        this.editingId = null;
        $('webhookFormHost').style.display = 'none';
        $('webhookForm').reset();
        this.resetForm();
    },

    async edit(id) {
        const item = await this.fetchItem(id);
        if (!item) return;
        this.editingId = id;
        $('webhookFormTitle').textContent = `编辑 Webhook：${item.name}`;
        $('webhookFormBtn').textContent = '保存修改';
        this.populateForm(item);
        $('webhookFormHost').style.display = 'flex';
    },

    async save() {
        const btn = $('webhookFormBtn');
        const origText = btn.textContent;
        const editingId = this.editingId;
        btn.disabled = true;
        btn.textContent = '处理中…';
        try {
            const data = this.getFormData();
            const r = await api(editingId ? 'PUT' : 'POST', editingId ? `${this.apiPath}/${editingId}` : this.apiPath, { body: data });
            if (!r.ok) { toast(r.data?.detail || r.error || '操作失败', 'error'); return; }
            toast(editingId ? 'Webhook 已更新' : 'Webhook 已创建', 'success');
            this.hideForm();
            await this.load();
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    },

    async remove(id) {
        if (!confirm('确定要删除这个 Webhook 吗？')) return;
        const r = await api('DELETE', `${this.apiPath}/${id}`);
        if (r.ok) {
            toast('Webhook 已删除', 'success');
            if (this.editingId === id) this.hideForm();
            await this.load();
        } else {
            toast(r.data?.detail || r.error || '删除失败', 'error');
        }
    },

    async toggle(id, current) {
        const r = await api('PUT', `${this.apiPath}/${id}`, { body: { enabled: !current } });
        if (r.ok) await this.load();
        else toast(r.data?.detail || r.error || '状态更新失败', 'error');
    },
};

$('webhookForm').addEventListener('submit', async e => {
    e.preventDefault();
    await webhookCrud.save();
});
