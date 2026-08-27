// ============================================================================
// custom_commands.js — 远程命令策略 + 自定义命令（两个独立 section）
// ============================================================================

let customCommandMessageComposer = null;

// ---------------------------------------------------------------- 远程命令策略
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
        this.draftAllowlist = [...allowlist];
        $('remoteCommandStats').innerHTML = [
            `<span class="policy-stat">远程命令 ${availableCommands.length}</span>`,
            `<span class="policy-stat">白名单 ${allowlist.length || '全部'}</span>`,
            `<span class="policy-stat">豁免群 ${bypassGroups.length}</span>`,
        ].join('');
        this.renderView();
    },

    renderView() {
        this.renderCommandList();
        if (this.editing) {
            updateRemoteCommandBypassGroups(this.bypassGroups);
        } else {
            $('remoteCommandBypassGroups').innerHTML = remotePolicyText(this.bypassGroups, '无');
        }
        if ($('remoteCommandEditBtn')) $('remoteCommandEditBtn').textContent = this.editing ? '取消' : '编辑';
        if ($('remoteCommandSaveBtn')) $('remoteCommandSaveBtn').style.display = this.editing ? '' : 'none';
    },

    renderCommandList() {
        const box = $('remoteCommandList');
        if (!box) return;
        const cmds = this.availableCommands || [];
        const allowlistSet = new Set((this.draftAllowlist || []).map(x => String(x).toLowerCase()));
        const unrestricted = !this.allowlist.length;
        if (!cmds.length) {
            box.innerHTML = '<div class="empty-hint">未加载到远程命令</div>';
            return;
        }
        if (this.editing) {
            box.innerHTML = cmds.map(c => {
                const key = String(c.key || '').trim();
                const desc = String(c.description || '').trim();
                const checked = allowlistSet.has(key.toLowerCase());
                return `<label class="checkbox-item"><input type="checkbox" value="${escapeHtml(key)}"${checked ? ' checked' : ''} onchange="remoteCommandPolicy.toggleAllowlist(this.value, this.checked)"><span>${escapeHtml(key)}${desc ? `<span class="checkbox-sub">${escapeHtml(desc)}</span>` : ''}</span></label>`;
            }).join('');
        } else {
            box.innerHTML = cmds.map(c => {
                const key = String(c.key || '').trim();
                const desc = String(c.description || '').trim();
                const whitelisted = unrestricted || allowlistSet.has(key.toLowerCase());
                const badge = unrestricted
                    ? '<span class="badge on">允许</span>'
                    : (whitelisted ? '<span class="badge on">白名单</span>' : '<span class="badge off">未白名单</span>');
                return `<div class="remote-cmd-row"><span class="remote-cmd-key">${escapeHtml(key)}</span><span class="remote-cmd-desc">${escapeHtml(desc)}</span>${badge}</div>`;
            }).join('');
        }
    },

    async load() {
        $('remoteCommandStats').innerHTML = '<span class="policy-stat">加载中…</span>';
        const r = await api('GET', this.apiPath);
        if (!r.ok) {
            this.loaded = false;
            $('remoteCommandSaveBtn').disabled = true;
            $('remoteCommandEditBtn').disabled = true;
            $('remoteCommandRefreshBtn').disabled = true;
            $('remoteCommandStats').innerHTML = '<span class="policy-stat">远程命令未启用</span>';
            $('remoteCommandList').innerHTML = '<div class="empty-hint">远程命令服务未启用</div>';
            $('remoteCommandBypassGroups').innerHTML = '无';
            return;
        }
        this.loaded = true;
        $('remoteCommandSaveBtn').disabled = false;
        $('remoteCommandEditBtn').disabled = false;
        $('remoteCommandRefreshBtn').disabled = false;
        this.render(r.data || {});
    },

    toggleEdit() {
        if (!this.loaded) return;
        this.editing = !this.editing;
        this.draftAllowlist = [...this.allowlist];
        this.renderView();
    },

    toggleAllowlist(key, checked) {
        const normalized = String(key).toLowerCase();
        const next = this.draftAllowlist.filter(item => String(item).toLowerCase() !== normalized);
        if (checked) next.push(key);
        this.draftAllowlist = next;
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
        btn.textContent = '刷新中…';
        try {
            const r = await api('POST', '/api/remote-commands/refresh');
            if (!r.ok) { toast(r.data?.detail || r.error || '远程命令列表刷新失败', 'error'); return; }
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
        btn.textContent = '保存中…';
        try {
            const r = await api('PUT', this.apiPath, { body: this.getPayload() });
            if (!r.ok) { toast(r.data?.detail || r.error || '远程命令策略保存失败', 'error'); return; }
            this.editing = false;
            this.render(r.data || {});
            toast('远程命令策略已保存', 'success');
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    },
};

// ---------------------------------------------------------------- 自定义命令
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
    container.innerHTML = allGroups.map(g => {
        const name = groupDisplayName(g);
        if (!name) return '';
        const checked = selected.has(name) ? ' checked' : '';
        return `<label class="checkbox-item"><input type="checkbox" value="${escapeHtml(name)}" name="customCommandTargetGroups"${checked}>${escapeHtml(name)}</label>`;
    }).join('');
}

function updateCustomCommandGroupFilterOptions() {
    const select = $('customCommandGroupFilter');
    if (!select) return;
    const selected = select.value || '__all__';
    const groups = new Set(allGroups.map(groupDisplayName).filter(Boolean));
    customCommandCrud.items.forEach(command => {
        (command.target_groups || []).filter(Boolean).forEach(group => groups.add(group));
    });
    select.replaceChildren(
        new Option('全部命令', '__all__'),
        new Option('仅全局命令', '__global__'),
        ...Array.from(groups).sort((a, b) => a.localeCompare(b, 'zh-CN')).map(group => new Option(group, group)),
    );
    select.value = Array.from(select.options).some(option => option.value === selected) ? selected : '__all__';
}

const customCommandCrud = {
    apiPath: '/api/custom-commands',
    items: [],
    editingId: null,

    renderItem(cmd) {
        const actionType = cmd.action_type || cmd.message_type || 'text';
        const icon = actionType === 'summary' ? '📊'
            : actionType === 'image' ? '🖼️'
            : actionType === 'file' ? '📎'
            : (actionType === 'voice' || actionType === 'voice_file') ? '🎙'
            : '💬';
        const targets = cmd.target_groups?.length ? `${cmd.target_groups.length} 个群` : '所有群';
        const preview = messagePreview(actionType, cmd.message, cmd.filename);
        return `<div class="res-item" data-command-id="${cmd.id}">
            <div class="res-icon">${icon}</div>
            <div class="res-body">
                <div class="res-title"><span class="res-name mono">${escapeHtml(cmd.key)}</span><span class="badge ${cmd.enabled ? 'on' : 'off'}">${cmd.enabled ? '已启用' : '已禁用'}</span></div>
                <div class="res-meta">
                    ${cmd.description ? `<span>${escapeHtml(cmd.description)}</span><span class="sep">·</span>` : ''}
                    <span>类型 ${escapeHtml(actionType)}</span><span class="sep">·</span>
                    <span>目标 ${escapeHtml(targets)}</span>
                </div>
                <div class="res-preview">${preview}</div>
            </div>
            <div class="res-actions">
                <button class="toggle${cmd.enabled ? ' on' : ''}" onclick="customCommandCrud.toggle(${cmd.id}, ${cmd.enabled})" title="${cmd.enabled ? '禁用' : '启用'}"></button>
                <button class="btn btn-ghost btn-sm" onclick="customCommandCrud.edit(${cmd.id})">编辑</button>
                <button class="btn btn-danger-ghost btn-sm" onclick="customCommandCrud.remove(${cmd.id})">删除</button>
            </div>
        </div>`;
    },

    filteredItems() {
        const group = $('customCommandGroupFilter')?.value || '__all__';
        if (group === '__all__') return this.items;
        if (group === '__global__') return this.items.filter(cmd => !cmd.target_groups?.length);
        return this.items.filter(cmd => !cmd.target_groups?.length || cmd.target_groups.includes(group));
    },

    render() {
        const el = $('customCommandsList');
        const items = this.filteredItems();
        const count = $('customCommandFilterCount');
        if (count) count.textContent = `${items.length} / ${this.items.length}`;
        if (!this.items.length) el.innerHTML = '<div class="empty-hint">暂无自定义命令</div>';
        else if (!items.length) el.innerHTML = '<div class="empty-hint">该筛选条件下暂无自定义命令</div>';
        else el.innerHTML = items.map(item => this.renderItem(item)).join('');
    },

    async load() {
        $('customCommandsList').innerHTML = '<div class="loading-hint">加载中…</div>';
        const r = await api('GET', this.apiPath);
        if (!r.ok) { toast(r.data?.detail || r.error || '加载自定义命令失败', 'error'); return; }
        this.items = Array.isArray(r.data) ? r.data : [];
        updateCustomCommandGroupFilterOptions();
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
        if (actionType === 'voice_file' && cmd.filename && cmd.message) {
            customCommandMessageComposer.setAttachment(cmd.message, cmd.filename);
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
        $('customCommandFormTitle').textContent = '新建自定义命令';
        $('customCommandFormBtn').textContent = '创建命令';
        this.resetForm();
        $('customCommandFormHost').style.display = 'flex';
        $('customCommandKey').focus();
    },

    hideForm() {
        this.editingId = null;
        $('customCommandFormHost').style.display = 'none';
        this.resetForm();
    },

    async edit(id) {
        const item = await this.fetchItem(id);
        if (!item) return;
        this.editingId = id;
        $('customCommandFormTitle').textContent = `编辑自定义命令：${item.key}`;
        $('customCommandFormBtn').textContent = '保存修改';
        this.populateForm(item);
        $('customCommandFormHost').style.display = 'flex';
    },

    async save() {
        const btn = $('customCommandFormBtn');
        const origText = btn.textContent;
        const editingId = this.editingId;
        btn.disabled = true;
        btn.textContent = '处理中…';
        try {
            const data = this.getFormData();
            if (!data) return;
            const r = await api(editingId ? 'PUT' : 'POST', editingId ? `${this.apiPath}/${editingId}` : this.apiPath, { body: data });
            if (!r.ok) { toast(r.data?.detail || r.error || '操作失败', 'error'); return; }
            toast(editingId ? '自定义命令已更新' : '自定义命令已创建', 'success');
            this.hideForm();
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

$('customCommandForm').addEventListener('submit', async e => {
    e.preventDefault();
    await customCommandCrud.save();
});

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
    isActive: () => $('section-custom-commands')?.classList.contains('active') && $('customCommandFormHost')?.style.display !== 'none',
});

registerGroupsLoadedHandler(() => {
    if (remoteCommandPolicy.loaded && remoteCommandPolicy.editing) {
        updateRemoteCommandBypassGroups(remoteCommandPolicy.bypassGroups);
    }
    updateCustomCommandGroupFilterOptions();
    customCommandCrud.render();
    if ($('customCommandFormHost')?.style.display !== 'none') updateCustomCommandTargetGroupsCheckboxes();
});
