let customCommandMessageComposer = null;

function parseRemoteCommandKeys(value) {
    return String(value || '')
        .split(/[\n,，]+/)
        .map(item => item.trim())
        .filter(Boolean);
}

function updateRemoteCommandBypassGroups(selectedValues = null) {
    const container = $('remoteCommandBypassGroups');
    if (!container) return;
    const selected = new Set(selectedValues || Array.from(document.querySelectorAll('input[name="remoteCommandBypassGroups"]:checked')).map(cb => cb.value));
    const groups = allGroups.map(groupDisplayName).filter(Boolean);
    if (!groups.length) {
        container.innerHTML = '<div class="help-text">未加载到群组，请稍后重试或检查 Gateway 连接</div>';
        return;
    }
    container.innerHTML = allGroups.map(g => {
        const name = groupDisplayName(g);
        if (!name) return '';
        const checked = selected.has(name) ? ' checked' : '';
        return `<label class="checkbox-item"><input type="checkbox" value="${escapeHtml(name)}" name="remoteCommandBypassGroups"${checked}>${escapeHtml(name)}</label>`;
    }).join('');
}

const remoteCommandPolicy = {
    apiPath: '/api/remote-commands/policy',
    loaded: false,
    bypassGroups: [],

    render(policy = {}) {
        const allowlist = policy.allowlist || [];
        const bypassGroups = policy.bypass_groups || [];
        this.bypassGroups = bypassGroups;
        $('remoteCommandAllowlist').value = allowlist.join('\n');
        updateRemoteCommandBypassGroups(bypassGroups);
        const unrestricted = !allowlist.length;
        const badge = $('remoteCommandPolicyBadge');
        badge.textContent = unrestricted ? '默认允许' : `白名单 ${allowlist.length}`;
        badge.classList.toggle('on', unrestricted);
        badge.classList.toggle('off', !unrestricted);
    },

    async load() {
        const r = await api('GET', this.apiPath);
        if (!r.ok) {
            this.loaded = false;
            $('remoteCommandPolicyBadge').textContent = '未启用';
            $('remoteCommandPolicyBadge').classList.remove('on');
            $('remoteCommandPolicyBadge').classList.add('off');
            $('remoteCommandSaveBtn').disabled = true;
            updateRemoteCommandBypassGroups();
            return;
        }
        this.loaded = true;
        $('remoteCommandSaveBtn').disabled = false;
        this.render(r.data || {});
    },

    getPayload() {
        return {
            allowlist: parseRemoteCommandKeys($('remoteCommandAllowlist').value),
            bypass_groups: Array.from(document.querySelectorAll('input[name="remoteCommandBypassGroups"]:checked')).map(cb => cb.value),
        };
    },

    async save() {
        const btn = $('remoteCommandSaveBtn');
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '保存中...';
        try {
            const r = await api('PUT', this.apiPath, { body: this.getPayload() });
            if (!r.ok) {
                toast(r.data?.detail || r.error || '远程命令策略保存失败', 'error');
                return;
            }
            this.render(r.data || {});
            toast('远程命令策略已保存', 'success');
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    },
};

function toggleCustomCommandMessageType(opts = {}) {
    if (customCommandMessageComposer) customCommandMessageComposer.syncType(opts);
}

function resetCustomCommandUpload() {
    if (customCommandMessageComposer) customCommandMessageComposer.resetUpload();
}

function updateCustomCommandTargetGroupsCheckboxes(selectedValues = null) {
    const container = $('customCommandTargetGroupsCheckboxes');
    if (!container) return;
    const selected = new Set(selectedValues || Array.from(document.querySelectorAll('input[name="customCommandTargetGroups"]:checked')).map(cb => cb.value));
    const groups = allGroups.map(groupDisplayName).filter(Boolean);
    if (!groups.length) {
        container.innerHTML = '<div class="help-text">未加载到群组，请稍后重试或检查 Gateway 连接</div>';
        return;
    }
    container.innerHTML = allGroups.map(g =>
        {
            const name = groupDisplayName(g);
            if (!name) return '';
            const checked = selected.has(name) ? ' checked' : '';
            return `<label class="checkbox-item"><input type="checkbox" value="${escapeHtml(name)}" name="customCommandTargetGroups"${checked}>${escapeHtml(name)}</label>`;
        }
    ).join('');
}

const customCommandCrud = {
    apiPath: '/api/custom-commands',
    items: [],
    editingId: null,

    init() {
        $('customCommandForm').addEventListener('submit', async e => {
            e.preventDefault();
            await this.save();
        });
    },

    renderItem(cmd) {
        let contentHtml;
        let contentLabel = '回复内容';
        let useRaw = false;
        const actionType = cmd.action_type || cmd.message_type || 'text';
        if (actionType === 'summary') {
            contentHtml = '触发群';
            contentLabel = '总结范围';
        } else if (actionType === 'image') {
            contentHtml = cmd.message ? `<img src="data:image/png;base64,${escapeHtml(cmd.message)}" style="max-width:160px;max-height:80px;border-radius:4px">` : '无图片';
            contentLabel = '图片';
            useRaw = true;
        } else if (actionType === 'file') {
            contentHtml = escapeHtml(cmd.filename || '未命名');
            contentLabel = '文件';
        } else {
            contentHtml = escapeHtml(cmd.message || '');
        }
        const contentItem = useRaw ? infoItemRaw(contentLabel, contentHtml) : infoItem(contentLabel, contentHtml);
        const targets = infoItem('目标群组', cmd.target_groups?.length ? cmd.target_groups.join(', ') : '所有群组');
        const editing = this.editingId === cmd.id;
        const editMount = editing ? `<div class="custom-command-edit-mount" id="customCommandEditMount-${cmd.id}"></div>` : '';
        return `<div class="crud-item${editing ? ' editing' : ''}" data-command-id="${cmd.id}">${itemHeader(cmd.key, cmd.enabled)}<div class="info-grid">${infoItem('描述', cmd.description || '-')}${infoItem('类型', actionType)}${contentItem}${targets}</div>${itemActions('customCommandCrud', cmd.id, cmd.enabled)}${editMount}</div>`;
    },

    render() {
        this.parkForm();
        const el = $('customCommandsList');
        if (!this.items.length) {
            el.innerHTML = '<div class="empty-hint">暂无自定义命令</div>';
        } else {
            el.innerHTML = this.items.map(item => this.renderItem(item)).join('');
        }
        if (this.editingId) this.mountEditForm(this.editingId);
    },

    parkForm() {
        const host = $('customCommandFormHost');
        const panel = $('customCommandFormPanel');
        if (host && panel && panel.parentElement !== host) host.appendChild(panel);
    },

    mountEditForm(id) {
        const mount = $(`customCommandEditMount-${id}`);
        const panel = $('customCommandFormPanel');
        if (mount && panel) mount.appendChild(panel);
    },

    async load() {
        const r = await api('GET', this.apiPath);
        if (!r.ok) {
            toast(r.data?.detail || r.error || '加载自定义命令失败', 'error');
            return;
        }
        this.items = Array.isArray(r.data) ? r.data : [];
        this.render();
    },

    async fetchItem(id) {
        const r = await api('GET', `${this.apiPath}/${id}`);
        if (r.ok) return r.data;
        toast(r.data?.detail || r.error || '读取自定义命令失败', 'error');
        return null;
    },

    getFormData() {
        const actionType = customCommandMessageComposer.getType();
        const data = {
            key: $('customCommandKey').value.trim(),
            description: $('customCommandDescription').value.trim(),
            action_type: actionType,
            filename: '',
            target_groups: Array.from(document.querySelectorAll('input[name="customCommandTargetGroups"]:checked')).map(cb => cb.value),
            enabled: $('customCommandEnabled').value === 'true',
        };
        if (!data.key) { toast('请输入命令 Key', 'error'); return null; }
        if (/\s/.test(data.key)) { toast('命令 Key 不能包含空白字符', 'error'); return null; }
        if (actionType === 'summary') {
            data.message = '';
            return data;
        }
        const payload = customCommandMessageComposer.getPayload({ text: '请输入回复内容', attachment: '请选择内容' });
        if (!payload) return null;
        data.message = payload.message;
        data.filename = payload.filename;
        return data;
    },

    populateForm(cmd) {
        $('customCommandKey').value = cmd.key;
        $('customCommandDescription').value = cmd.description || '';
        $('customCommandEnabled').value = cmd.enabled.toString();
        const actionType = cmd.action_type || cmd.message_type || 'text';
        $('customCommandMessageType').value = actionType;
        $('customCommandMessage').value = actionType === 'text' ? cmd.message : '';
        resetCustomCommandUpload();
        if (actionType === 'image' && cmd.message) {
            customCommandMessageComposer.setAttachment(cmd.message, '', 'data:image/png;base64,' + cmd.message);
        }
        if (actionType === 'file' && cmd.message) {
            customCommandMessageComposer.setAttachment(cmd.message, cmd.filename || '');
        }
        updateCustomCommandTargetGroupsCheckboxes(cmd.target_groups || []);
        toggleCustomCommandMessageType({ resetUpload: false });
    },

    resetForm() {
        $('customCommandForm').reset();
        resetCustomCommandUpload();
        $('customCommandMessageType').value = 'text';
        $('customCommandEnabled').value = 'true';
        updateCustomCommandTargetGroupsCheckboxes([]);
        toggleCustomCommandMessageType();
    },

    showForm() {
        this.editingId = null;
        this.render();
        $('customCommandFormTitle').textContent = '新建自定义命令';
        $('customCommandFormBtn').textContent = '创建命令';
        this.resetForm();
        this.parkForm();
        $('customCommandFormPanel').style.display = '';
        $('customCommandKey').focus();
    },

    hideForm(opts = {}) {
        this.editingId = null;
        $('customCommandFormPanel').style.display = 'none';
        this.parkForm();
        this.resetForm();
        if (opts.render !== false) this.render();
    },

    async edit(id) {
        const item = await this.fetchItem(id);
        if (!item) return;
        this.editingId = id;
        this.render();
        $('customCommandFormTitle').textContent = `编辑自定义命令：${item.key}`;
        $('customCommandFormBtn').textContent = '保存修改';
        this.populateForm(item);
        this.mountEditForm(id);
        $('customCommandFormPanel').style.display = '';
        $('customCommandFormPanel').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    },

    async save() {
        const btn = $('customCommandFormBtn');
        const origText = btn.textContent;
        const editingId = this.editingId;
        btn.disabled = true;
        btn.textContent = '处理中...';
        try {
            const data = this.getFormData();
            if (!data) return;
            const r = await api(editingId ? 'PUT' : 'POST', editingId ? `${this.apiPath}/${editingId}` : this.apiPath, { body: data });
            if (!r.ok) {
                toast(r.data?.detail || r.error || '操作失败', 'error');
                return;
            }
            toast(editingId ? '自定义命令已更新' : '自定义命令已创建', 'success');
            this.hideForm({ render: false });
            await this.load();
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    },

    async remove(id) {
        if (!confirm('确定要删除这个自定义命令吗？')) return;
        const r = await api('DELETE', `${this.apiPath}/${id}`);
        if (r.ok) {
            toast('自定义命令已删除', 'success');
            if (this.editingId === id) this.hideForm({ render: false });
            await this.load();
        } else {
            toast(r.data?.detail || r.error || '删除失败', 'error');
        }
    },

    async toggle(id, current) {
        const r = await api('PUT', `${this.apiPath}/${id}`, { body: { enabled: !current } });
        if (r.ok) {
            await this.load();
        } else {
            toast(r.data?.detail || r.error || '状态更新失败', 'error');
        }
    },
};

customCommandCrud.init();
remoteCommandPolicy.load();

customCommandMessageComposer = new MessageComposer({
    typeId: 'customCommandMessageType',
    textGroupId: 'customCommandTextGroup',
    textInputId: 'customCommandMessage',
    attachmentGroupId: 'customCommandAttachmentGroup',
    fileInputId: 'customCommandFileSelect',
    dropzoneId: 'customCommandFileDropzone',
    previewId: 'customCommandFilePreview',
    infoId: 'customCommandFileInfo',
    isActive: () => $('custom-commands-panel')?.classList.contains('active') && $('customCommandFormPanel')?.style.display !== 'none',
});

if (typeof registerGroupsLoadedHandler === 'function') {
    registerGroupsLoadedHandler(() => {
        updateRemoteCommandBypassGroups(remoteCommandPolicy.bypassGroups);
        if ($('customCommandFormPanel')?.style.display !== 'none') updateCustomCommandTargetGroupsCheckboxes();
    });
}
