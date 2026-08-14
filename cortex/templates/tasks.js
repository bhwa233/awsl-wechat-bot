// ============================================================================
// tasks.js — 定时任务模块（Cron 调度 + 消息/总结负载）
// ============================================================================

let taskMessageComposer = null;
let taskTargetGroupSelection = [];

// 消息内容预览（图片显示缩略图，其余显示文字）
function messagePreview(type, message, filename) {
    if (type === 'summary') return '<span class="preview-text">📊 群聊总结</span>';
    if (type === 'image') {
        if (message) return `<img class="res-preview-img" src="data:image/png;base64,${escapeHtml(message)}" alt="图片预览">`;
        return '<span class="preview-text">🖼️ 无图片</span>';
    }
    if (type === 'file') return `<span class="preview-text">📎 ${escapeHtml(filename || '未命名文件')}</span>`;
    if (type === 'voice') return `<span class="preview-text">🎙 “${escapeHtml(message || '')}”</span>`;
    if (type === 'voice_file') return `<span class="preview-text">🎙 ${escapeHtml(filename || '语音文件')}</span>`;
    return `<span class="preview-text">“${escapeHtml(message || '')}”</span>`;
}

function loadTaskServerInfo() {
    api('GET', '/api/health').then(r => {
        if (!r.ok || !r.data) return;
        const info = r.data;
        const el = $('timezoneInfo');
        if (!el) return;
        if (info.timezone && info.server_time) {
            const t = new Date(info.server_time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
            const offset = info.timezone_offset ? `UTC${info.timezone_offset}` : '';
            const timezone = info.timezone.includes('?') ? offset : `${info.timezone}${offset ? ` (${offset})` : ''}`;
            el.textContent = `服务器时区: ${timezone} · 当前时间: ${t}`;
        }
    });
}

async function toggleTaskFormMode(opts = {}) {
    if ($('taskType').value === 'summary') $('taskMessageType').value = 'summary';
    toggleTaskMessageType(opts);
    if ($('taskMessageType').value === 'summary') await loadSummaryGroupOptions();
}

let summaryGroupOptionsLoaded = false;
let summaryGroupOptions = [];

async function loadSummaryGroupOptions() {
    if (summaryGroupOptionsLoaded) return;
    const r = await api('GET', '/api/chatlog/groups');
    if (r.ok && r.data) {
        summaryGroupOptions = r.data;
        summaryGroupOptionsLoaded = true;
    }
}

function buildGroupOptionHtml(selectedValue) {
    return summaryGroupOptions.map(g => {
        const name = g.display_name || g.nick_name || g.username;
        const sel = g.username === selectedValue ? ' selected' : '';
        return `<option value="${escapeHtml(g.username)}"${sel}>${escapeHtml(name)}</option>`;
    }).join('');
}

function buildGatewayGroupOptionHtml(selectedValue) {
    const names = allGroups.map(groupDisplayName).filter(Boolean);
    const legacy = selectedValue && !names.includes(selectedValue)
        ? `<option value="${escapeHtml(selectedValue)}" selected>${escapeHtml(selectedValue)}</option>`
        : '';
    return legacy + names.map(name => {
        const sel = name === selectedValue ? ' selected' : '';
        return `<option value="${escapeHtml(name)}"${sel}>${escapeHtml(name)}</option>`;
    }).join('');
}

function addSummaryRow(groupId = '', groupName = '', containerId = 'summaryRows') {
    const container = $(containerId);
    const row = document.createElement('div');
    row.className = 'summary-row';
    row.innerHTML = `
        <select class="summary-source" onchange="syncSummaryRowName(this)">
            <option value="">来源群聊…</option>
            ${buildGroupOptionHtml(groupId)}
        </select>
        <select class="summary-target">
            <option value="">发送到…</option>
            ${buildGatewayGroupOptionHtml(groupName)}
        </select>
        <button type="button" class="remove-row" onclick="this.parentElement.remove()">×</button>
    `;
    container.appendChild(row);
}

function refreshSummaryTargetOptions(containerId = 'summaryRows') {
    document.querySelectorAll(`#${containerId} .summary-target`).forEach(select => {
        const selected = select.value;
        select.innerHTML = `<option value="">发送到…</option>${buildGatewayGroupOptionHtml(selected)}`;
    });
}

function syncSummaryRowName(sourceSelect) {
    const row = sourceSelect.closest('.summary-row');
    const targetSelect = row.querySelector('.summary-target');
    const opt = sourceSelect.selectedOptions[0];
    if (!opt || !opt.value) return;
    const name = opt.textContent.trim();
    for (const o of targetSelect.options) {
        if (o.textContent.trim() === name) { targetSelect.value = o.value; break; }
    }
}

function getSummaryConfig(containerId = 'summaryRows') {
    const rows = document.querySelectorAll(`#${containerId} .summary-row`);
    const groups = [];
    for (const row of rows) {
        const source = row.querySelector('.summary-source').value;
        const target = row.querySelector('.summary-target').value;
        if (!source || !target) continue;
        const opt = row.querySelector('.summary-source').selectedOptions[0];
        groups.push({ group_id: source, group_name: target });
    }
    return { groups };
}

function populateSummaryRows(configStr, containerId = 'summaryRows') {
    const container = $(containerId);
    container.innerHTML = '';
    loadSummaryGroupOptions().then(() => {
        try {
            const cfg = JSON.parse(configStr);
            if (cfg.groups && Array.isArray(cfg.groups)) {
                for (const g of cfg.groups) addSummaryRow(g.group_id || '', g.group_name || '', containerId);
            }
        } catch { addSummaryRow('', '', containerId); }
    });
}

function toggleTaskMessageType(opts = {}) {
    if (taskMessageComposer) taskMessageComposer.syncType(opts);
}
function resetTaskUpload() {
    if (taskMessageComposer) taskMessageComposer.resetUpload();
}

function updateTargetGroupsCheckboxes(selectedValues = null) {
    const container = $('targetGroupsCheckboxes');
    if (!container) return;
    const renderedInputs = Array.from(container.querySelectorAll('input[name="targetGroups"]'));
    const checkedValues = renderedInputs.map(cb => cb.checked ? cb.value : null).filter(Boolean);
    if (selectedValues !== null) taskTargetGroupSelection = Array.from(selectedValues || []);
    else if (renderedInputs.length) taskTargetGroupSelection = checkedValues;
    const selected = new Set(taskTargetGroupSelection);
    const groups = allGroups.map(groupDisplayName).filter(Boolean);
    if (!groups.length) {
        container.innerHTML = '<div class="help-text">未加载到群组，请稍后重试或检查 Gateway 连接</div>';
        return;
    }
    container.innerHTML = allGroups.map(g => {
        const name = groupDisplayName(g);
        if (!name) return '';
        const checked = selected.has(name) ? ' checked' : '';
        return `<label class="checkbox-item"><input type="checkbox" value="${escapeHtml(name)}" name="targetGroups"${checked}>${escapeHtml(name)}</label>`;
    }).join('');
}

const taskCrud = {
    apiPath: '/api/tasks',
    items: [],
    editingId: null,

    renderItem(task) {
        const isSummary = task.task_type === 'summary';
        const typeLabel = isSummary ? '群聊总结' : (task.message_type || 'text');
        const icon = isSummary ? '📊'
            : task.message_type === 'image' ? '🖼️'
            : task.message_type === 'file' ? '📎'
            : (task.message_type === 'voice' || task.message_type === 'voice_file') ? '🎙'
            : '💬';
        const targetLabel = isSummary ? '触发群' : (task.target_groups?.length ? `${task.target_groups.length} 个群` : '所有群');
        const lastRun = task.last_run ? formatTime(task.last_run) : '从未';
        let preview = '';
        if (isSummary) {
            try { const c = JSON.parse(task.message); const names = (c.groups || []).map(g => g.group_name).filter(Boolean); preview = `<span class="preview-text">📊 总结 ${names.length ? names.join('、') : ''}</span>`; }
            catch { preview = '<span class="preview-text">📊 群聊总结</span>'; }
        } else {
            preview = messagePreview(task.message_type || 'text', task.message, task.filename);
        }
        return `<div class="res-item" data-task-id="${task.id}">
            <div class="res-icon">${icon}</div>
            <div class="res-body">
                <div class="res-title"><span class="res-name">${escapeHtml(task.name)}</span><span class="badge ${task.enabled ? 'on' : 'off'}">${task.enabled ? '已启用' : '已禁用'}</span></div>
                <div class="res-meta">
                    <span class="mono">${escapeHtml(task.cron_expression)}</span><span class="sep">·</span>
                    <span>${escapeHtml(typeLabel)}</span><span class="sep">·</span>
                    <span>${escapeHtml(targetLabel)}</span><span class="sep">·</span>
                    <span>最后运行 ${lastRun}</span>
                </div>
                <div class="res-preview">${preview}</div>
            </div>
            <div class="res-actions">
                <button class="toggle${task.enabled ? ' on' : ''}" onclick="taskCrud.toggle(${task.id}, ${task.enabled})" title="${task.enabled ? '禁用' : '启用'}"></button>
                <button class="btn btn-ghost btn-sm" onclick="taskCrud.edit(${task.id})">编辑</button>
                <button class="btn btn-danger-ghost btn-sm" onclick="taskCrud.remove(${task.id})">删除</button>
            </div>
        </div>`;
    },

    render() {
        const el = $('tasksList');
        if (!this.items.length) el.innerHTML = '<div class="empty-hint">暂无定时任务</div>';
        else el.innerHTML = this.items.map(item => this.renderItem(item)).join('');
    },

    async load() {
        $('tasksList').innerHTML = '<div class="loading-hint">加载中…</div>';
        const r = await api('GET', this.apiPath);
        if (!r.ok) { toast(r.data?.detail || r.error || '加载定时任务失败', 'error'); return; }
        this.items = Array.isArray(r.data) ? r.data : [];
        this.render();
    },

    async fetchItem(id) {
        const r = await api('GET', `/api/tasks/${id}`);
        if (r.ok) return r.data;
        toast(r.data?.detail || r.error || '读取定时任务失败', 'error');
        return null;
    },

    getFormData() {
        const composerType = taskMessageComposer.getType();
        const taskType = composerType === 'summary' ? 'summary' : 'send';
        const data = {
            name: $('taskName').value, cron_expression: $('cronExpression').value,
            task_type: taskType, enabled: $('taskEnabled').value === 'true',
            target_groups: Array.from(document.querySelectorAll('input[name="targetGroups"]:checked')).map(cb => cb.value),
        };
        if (taskType === 'summary') {
            const cfg = getSummaryConfig();
            if (!cfg.groups.length) { toast('请至少添加一个群聊', 'error'); return null; }
            data.message = JSON.stringify(cfg);
            data.message_type = 'text'; data.filename = ''; data.target_groups = [];
            return data;
        }
        const payload = taskMessageComposer.getPayload({ text: '请输入消息', attachment: '请选择内容' });
        if (!payload) return null;
        data.message_type = payload.message_type;
        data.message = payload.message;
        data.filename = payload.filename;
        return data;
    },

    populateForm(task) {
        $('taskName').value = task.name;
        $('cronExpression').value = task.cron_expression;
        $('taskEnabled').value = task.enabled.toString();
        resetTaskUpload();
        $('taskType').value = task.task_type || 'send';
        $('taskMessageType').value = task.task_type === 'summary' ? 'summary' : (task.message_type || 'text');
        if (task.task_type === 'summary') { populateSummaryRows(task.message); $('taskMessage').value = ''; }
        else {
            $('summaryRows').innerHTML = '';
            $('taskMessage').value = ['text', 'voice'].includes(task.message_type) ? task.message : '';
        }
        if (task.task_type === 'send' && task.message_type === 'image' && task.message) {
            taskMessageComposer.setAttachment(task.message, '', 'data:image/png;base64,' + task.message);
        }
        if (task.task_type === 'send' && task.message_type === 'file' && task.message) {
            taskMessageComposer.setAttachment(task.message, task.filename || '');
        }
        if (task.task_type === 'send' && task.message_type === 'voice_file' && task.filename && task.message) {
            taskMessageComposer.setAttachment(task.message, task.filename);
        }
        updateTargetGroupsCheckboxes(task.task_type === 'send' ? (task.target_groups || []) : []);
        toggleTaskFormMode({ resetUpload: false });
    },

    resetForm() {
        $('taskForm').reset();
        resetTaskUpload();
        $('taskType').value = 'send'; $('taskMessageType').value = 'text'; $('summaryRows').innerHTML = '';
        $('taskEnabled').value = 'true';
        updateTargetGroupsCheckboxes([]);
        toggleTaskFormMode();
    },

    showForm() {
        this.editingId = null;
        $('taskFormTitle').textContent = '新建定时任务';
        $('taskFormBtn').textContent = '创建任务';
        this.resetForm();
        $('taskFormHost').style.display = 'flex';
        $('taskName').focus();
    },

    hideForm() {
        this.editingId = null;
        $('taskFormHost').style.display = 'none';
        this.resetForm();
    },

    async edit(id) {
        const item = await this.fetchItem(id);
        if (!item) return;
        this.editingId = id;
        $('taskFormTitle').textContent = `编辑定时任务：${item.name}`;
        $('taskFormBtn').textContent = '保存修改';
        this.populateForm(item);
        $('taskFormHost').style.display = 'flex';
    },

    async save() {
        const btn = $('taskFormBtn');
        const origText = btn.textContent;
        const editingId = this.editingId;
        btn.disabled = true;
        btn.textContent = '处理中…';
        try {
            const data = this.getFormData();
            if (!data) return;
            const r = await api(editingId ? 'PUT' : 'POST', editingId ? `${this.apiPath}/${editingId}` : this.apiPath, { body: data });
            if (!r.ok) { toast(r.data?.detail || r.error || '操作失败', 'error'); return; }
            toast(editingId ? '任务已更新' : '任务已创建', 'success');
            this.hideForm();
            await this.load();
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    },

    async remove(id) {
        if (!confirm('确定要删除这个任务吗？')) return;
        const r = await api('DELETE', `${this.apiPath}/${id}`);
        if (r.ok) {
            toast('任务已删除', 'success');
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

$('taskForm').addEventListener('submit', async e => {
    e.preventDefault();
    await taskCrud.save();
});

taskMessageComposer = new MessageComposer({
    typeId: 'taskMessageType',
    textGroupId: 'taskTextGroup',
    textInputId: 'taskMessage',
    attachmentGroupId: 'taskAttachmentGroup',
    fileInputId: 'taskFileSelect',
    dropzoneId: 'taskFileDropzone',
    previewId: 'taskFilePreview',
    infoId: 'taskFileInfo',
    summaryGroupId: 'taskSummaryGroup',
    isActive: () => $('section-tasks')?.classList.contains('active') && $('taskFormHost')?.style.display !== 'none',
    onTypeChange: type => {
        $('taskType').value = type === 'summary' ? 'summary' : 'send';
        $('taskTargetGroupsGroup').style.display = type === 'summary' ? 'none' : '';
        if (type === 'summary') loadSummaryGroupOptions();
    },
});

registerGroupsLoadedHandler(() => {
    if ($('taskFormHost')?.style.display !== 'none') {
        updateTargetGroupsCheckboxes();
        refreshSummaryTargetOptions();
    }
});
