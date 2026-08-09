let customCommandMessageComposer = null;

function updateRemoteCommandAllowlist(commands = [], selectedValues = [], query = '') {
    const container = $('remoteCommandAllowlist');
    if (!container) return;
    const selected = new Set((selectedValues || []).map(value => String(value).toLowerCase()));
    const seen = new Set();
    const available = new Set();
    const items = [];
    const q = String(query || '').trim().toLowerCase();
    for (const command of commands || []) {
        const key = String(command?.key || '').trim();
        if (!key) continue;
        available.add(key.toLowerCase());
        const description = String(command?.description || '').trim();
        if (q && !key.toLowerCase().includes(q) && !description.toLowerCase().includes(q)) continue;
        seen.add(key.toLowerCase());
        items.push({
            key,
            description,
            checked: selected.has(key.toLowerCase()),
        });
    }
    for (const key of selectedValues || []) {
        const normalized = String(key).toLowerCase();
        if (!normalized || seen.has(normalized) || available.has(normalized)) continue;
        items.push({ key, description: '当前远程列表未返回', checked: true });
    }
    if (!items.length) {
        container.innerHTML = `<div class="help-text">${q ? '没有匹配的远程命令' : '未加载到远程命令'}</div>`;
        return;
    }
    container.innerHTML = items.map(item => {
        const checked = item.checked ? ' checked' : '';
        const description = item.description ? `<span class="checkbox-sub">${escapeHtml(item.description)}</span>` : '';
        return `<label class="checkbox-item"><input type="checkbox" value="${escapeHtml(item.key)}" name="remoteCommandAllowlist"${checked} onchange="remoteCommandPolicy.toggleAllowlist(this.value,this.checked)"><span>${escapeHtml(item.key)}${description}</span></label>`;
    }).join('');
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

function remotePolicyText(values, emptyText) {
    if (!values?.length) return escapeHtml(emptyText);
    return values.map(value => `<span class="policy-chip">${escapeHtml(value)}</span>`).join('');
}

const remoteCommandPolicy = {
    apiPath: '/api/remote-commands/policy',
    loaded: false,
    editing: false,
    allowlist: [],
    draftAllowlist: [],
    availableCommands: [],
    bypassGroups: [],

    render(policy = {}) {
        const allowlist = policy.allowlist || [];
        const bypassGroups = policy.bypass_groups || [];
        const availableCommands = policy.available_commands || [];
        this.allowlist = allowlist;
        this.availableCommands = availableCommands;
        this.bypassGroups = bypassGroups;
        $('remoteCommandAllowlistView').innerHTML = remotePolicyText(allowlist, '全部远程命令');
        $('remoteCommandBypassView').innerHTML = remotePolicyText(bypassGroups, '无');
        $('remoteCommandStats').innerHTML = [
            `<span class="policy-stat">远程命令 ${availableCommands.length}</span>`,
            `<span class="policy-stat">白名单 ${allowlist.length || '全部'}</span>`,
            `<span class="policy-stat">豁免群 ${bypassGroups.length}</span>`,
        ].join('');
        if (this.editing) {
            this.renderAllowlist();
            updateRemoteCommandBypassGroups(bypassGroups);
        }
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
            $('remoteCommandEditBtn').disabled = true;
            $('remoteCommandRefreshBtn').disabled = true;
            $('remoteCommandAllowlistView').textContent = '远程命令服务未启用';
            $('remoteCommandBypassView').textContent = '无';
            $('remoteCommandStats').innerHTML = '<span class="policy-stat">远程命令未启用</span>';
            this.hideEdit();
            return;
        }
        this.loaded = true;
        $('remoteCommandSaveBtn').disabled = false;
        $('remoteCommandEditBtn').disabled = false;
        $('remoteCommandRefreshBtn').disabled = false;
        this.hideEdit();
        this.render(r.data || {});
    },

    showEdit() {
        if (!this.loaded) return;
        this.editing = true;
        this.draftAllowlist = [...this.allowlist];
        $('remoteCommandSearch').value = '';
        $('remoteCommandPolicyView').style.display = 'none';
        $('remoteCommandPolicyEdit').style.display = '';
        this.renderAllowlist();
        updateRemoteCommandBypassGroups(this.bypassGroups);
    },

    hideEdit() {
        this.editing = false;
        $('remoteCommandPolicyEdit').style.display = 'none';
        $('remoteCommandPolicyView').style.display = '';
    },

    renderAllowlist() {
        updateRemoteCommandAllowlist(this.availableCommands, this.draftAllowlist, $('remoteCommandSearch')?.value || '');
    },

    toggleAllowlist(key, checked) {
        const normalized = String(key).toLowerCase();
        const next = this.draftAllowlist.filter(item => String(item).toLowerCase() !== normalized);
        if (checked) next.push(key);
        this.draftAllowlist = next;
    },

    clearAllowlist() {
        this.draftAllowlist = [];
        this.renderAllowlist();
    },

    getPayload() {
        return {
            allowlist: this.draftAllowlist,
            bypass_groups: Array.from(document.querySelectorAll('input[name="remoteCommandBypassGroups"]:checked')).map(cb => cb.value),
        };
    },

    async refreshCommands() {
        const btn = $('remoteCommandRefreshBtn');
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '刷新中...';
        try {
            const r = await api('POST', '/api/remote-commands/refresh');
            if (!r.ok) {
                toast(r.data?.detail || r.error || '远程命令列表刷新失败', 'error');
                return;
            }
            this.hideEdit();
            this.render(r.data || {});
            toast('远程命令列表已刷新', 'success');
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
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
            this.hideEdit();
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
        } else if (actionType === 'voice') {
            contentHtml = escapeHtml(cmd.message || '');
            contentLabel = '语音文本';
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
        $('customCommandMessage').value = ['text', 'voice'].includes(actionType) ? cmd.message : '';
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
        if (remoteCommandPolicy.editing) {
            remoteCommandPolicy.renderAllowlist();
            updateRemoteCommandBypassGroups(remoteCommandPolicy.bypassGroups);
        }
        if ($('customCommandFormPanel')?.style.display !== 'none') updateCustomCommandTargetGroupsCheckboxes();
    });
}
