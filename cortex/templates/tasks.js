let taskMessageComposer = null;
let taskTargetGroupSelection = [];

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
    const taskType = $('taskType').value;
    if (taskType === 'summary') {
        $('taskMessageType').value = 'summary';
    }
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
            <option value="">来源群聊...</option>
            ${buildGroupOptionHtml(groupId)}
        </select>
        <select class="summary-target">
            <option value="">发送到...</option>
            ${buildGatewayGroupOptionHtml(groupName)}
        </select>
        <button type="button" class="remove-row" onclick="this.parentElement.remove()">×</button>
    `;
    container.appendChild(row);
}

function refreshSummaryTargetOptions(containerId = 'summaryRows') {
    document.querySelectorAll(`#${containerId} .summary-target`).forEach(select => {
        const selected = select.value;
        select.innerHTML = `<option value="">发送到...</option>${buildGatewayGroupOptionHtml(selected)}`;
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
    if (selectedValues !== null) {
        taskTargetGroupSelection = Array.from(selectedValues || []);
    } else if (renderedInputs.length) {
        taskTargetGroupSelection = checkedValues;
    }
    const selected = new Set(taskTargetGroupSelection);
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
            return `<label class="checkbox-item"><input type="checkbox" value="${escapeHtml(name)}" name="targetGroups"${checked}>${escapeHtml(name)}</label>`;
        }
    ).join('');
}

function infoItem(label, value, style) {
    const safe = typeof value === 'string' ? escapeHtml(value) : value;
    return `<div class="info-item"><div class="info-label">${escapeHtml(label)}</div><div class="info-value"${style ? ` style="${style}"` : ''}>${safe}</div></div>`;
}

function infoItemRaw(label, html, style) {
    return `<div class="info-item"><div class="info-label">${escapeHtml(label)}</div><div class="info-value"${style ? ` style="${style}"` : ''}>${html}</div></div>`;
}

function itemHeader(name, enabled) {
    return `<div class="item-header"><div class="item-title">${escapeHtml(name)}</div><span class="badge ${enabled ? 'on' : 'off'}">${enabled ? '已启用' : '已禁用'}</span></div>`;
}

function itemActions(crudName, id, enabled, extra) {
    return `<div class="item-actions">${extra || ''}<button class="btn btn-ghost btn-sm" onclick="${crudName}.edit(${id})">编辑</button><button class="btn btn-ghost btn-sm" onclick="${crudName}.toggle(${id}, ${enabled})">${enabled ? '禁用' : '启用'}</button><button class="btn btn-red btn-sm" onclick="${crudName}.remove(${id})">删除</button></div>`;
}

class CrudManager {
    constructor(cfg) {
        this.cfg = cfg;
        this.editingId = null;
        $(cfg.formId).addEventListener('submit', async e => {
            e.preventDefault();
            const btn = $(cfg.formId.replace('Form', 'FormBtn'));
            const origText = btn.textContent;
            btn.disabled = true;
            btn.textContent = '处理中...';
            try {
                const data = cfg.getFormData();
                if (!data) return;
                const url = this.editingId ? `${cfg.apiPath}/${this.editingId}` : cfg.apiPath;
                const method = this.editingId ? 'PUT' : 'POST';
                const r = await api(method, url, { body: data });
                if (r.ok) { toast(this.editingId ? cfg.updateMsg : cfg.createMsg, 'success'); this.hideForm(); }
                else toast(r.data?.detail || r.error || '操作失败', 'error');
            } finally { await this.load(); btn.disabled = false; btn.textContent = origText; }
        });
    }

    async load() {
        const c = this.cfg;
        const r = await api('GET', c.apiPath);
        if (!r.ok) return;
        const el = $(c.listId);
        if (!r.data.length) { el.innerHTML = `<div class="empty-hint">${c.emptyText}</div>`; return; }
        el.innerHTML = r.data.map(item => c.renderItem(item)).join('');
    }

    showForm() {
        this.editingId = null;
        const c = this.cfg;
        $(c.formTitleId).textContent = c.createTitle;
        $(c.formId).reset();
        if (c.resetForm) c.resetForm();
        $(c.formPanelId).style.display = '';
    }

    hideForm() {
        $(this.cfg.formPanelId).style.display = 'none';
        $(this.cfg.formId).reset();
        if (this.cfg.resetForm) this.cfg.resetForm();
        this.editingId = null;
    }

    async edit(id) {
        const item = await this.cfg.fetchItem(id);
        if (!item) return;
        this.editingId = id;
        $(this.cfg.formTitleId).textContent = this.cfg.editTitle;
        this.cfg.populateForm(item);
        $(this.cfg.formPanelId).style.display = '';
    }

    async remove(id) {
        if (!confirm(this.cfg.deleteConfirm)) return;
        const r = await api('DELETE', `${this.cfg.apiPath}/${id}`);
        if (r.ok) { toast(this.cfg.deleteMsg, 'success'); this.load(); }
        else toast('删除失败', 'error');
    }

    async toggle(id, current) {
        const r = await api('PUT', `${this.cfg.apiPath}/${id}`, { body: { enabled: !current } });
        if (r.ok) this.load();
    }
}

const taskCrud = {
    apiPath: '/api/tasks',
    items: [],
    editingId: null,

    init() {
        $('taskForm').addEventListener('submit', async e => {
            e.preventDefault();
            await this.save();
        });
    },

    renderItem(task) {
        let typeDisplay = task.task_type === 'summary' ? '群聊总结' : '发送消息';
        let contentHtml, contentLabel = '消息内容';
        let useRaw = false;
        if (task.task_type === 'summary') {
            try { const c = JSON.parse(task.message); contentHtml = c.groups ? escapeHtml(c.groups.map(g => g.group_name).join(', ')) : '未配置'; }
            catch { contentHtml = '配置解析失败'; }
            contentLabel = '总结配置';
        } else if (task.message_type === 'image') {
            contentHtml = task.message ? `<img src="data:image/png;base64,${escapeHtml(task.message)}" style="max-width:160px;max-height:80px;border-radius:4px">` : '无图片';
            contentLabel = '图片';
            useRaw = true;
        } else if (task.message_type === 'file') {
            contentHtml = escapeHtml(task.filename || '未命名');
            contentLabel = '文件';
        } else {
            contentHtml = escapeHtml(task.message || '');
        }
        const contentItem = useRaw ? infoItemRaw(contentLabel, contentHtml) : infoItem(contentLabel, contentHtml);
        const targets = task.task_type === 'send' ? infoItem('目标群组', task.target_groups?.length ? task.target_groups.join(', ') : '所有群组') : '';
        const editing = this.editingId === task.id;
        const editMount = editing ? `<div class="task-edit-mount" id="taskEditMount-${task.id}"></div>` : '';
        return `<div class="crud-item${editing ? ' editing' : ''}" data-task-id="${task.id}">${itemHeader(task.name, task.enabled)}<div class="info-grid">${infoItem('Cron', task.cron_expression)}${infoItem('类型', typeDisplay)}${infoItem('最后运行', task.last_run || '从未')}${contentItem}${targets}</div>${itemActions('taskCrud', task.id, task.enabled)}${editMount}</div>`;
    },

    render() {
        this.parkForm();
        const el = $('tasksList');
        if (!this.items.length) {
            el.innerHTML = '<div class="empty-hint">暂无定时任务</div>';
        } else {
            el.innerHTML = this.items.map(item => this.renderItem(item)).join('');
        }
        if (this.editingId) this.mountEditForm(this.editingId);
    },

    parkForm() {
        const host = $('taskFormHost');
        const panel = $('taskFormPanel');
        if (host && panel && panel.parentElement !== host) host.appendChild(panel);
    },

    mountEditForm(id) {
        const mount = $(`taskEditMount-${id}`);
        const panel = $('taskFormPanel');
        if (mount && panel) mount.appendChild(panel);
    },

    async load() {
        const r = await api('GET', this.apiPath);
        if (!r.ok) {
            toast(r.data?.detail || r.error || '加载定时任务失败', 'error');
            return;
        }
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
        else { $('summaryRows').innerHTML = ''; $('taskMessage').value = task.message_type === 'text' ? task.message : ''; }
        if (task.task_type === 'send' && task.message_type === 'image' && task.message) {
            taskMessageComposer.setAttachment(task.message, '', 'data:image/png;base64,' + task.message);
        }
        if (task.task_type === 'send' && task.message_type === 'file' && task.message) {
            taskMessageComposer.setAttachment(task.message, task.filename || '');
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
        this.render();
        $('taskFormTitle').textContent = '新建定时任务';
        $('taskFormBtn').textContent = '创建任务';
        this.resetForm();
        this.parkForm();
        $('taskFormPanel').style.display = '';
        $('taskName').focus();
    },

    hideForm(opts = {}) {
        this.editingId = null;
        $('taskFormPanel').style.display = 'none';
        this.parkForm();
        this.resetForm();
        if (opts.render !== false) this.render();
    },

    async edit(id) {
        const item = await this.fetchItem(id);
        if (!item) return;
        this.editingId = id;
        this.render();
        $('taskFormTitle').textContent = `编辑定时任务：${item.name}`;
        $('taskFormBtn').textContent = '保存修改';
        this.populateForm(item);
        this.mountEditForm(id);
        $('taskFormPanel').style.display = '';
        $('taskFormPanel').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    },

    async save() {
        const btn = $('taskFormBtn');
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
            toast(editingId ? '任务已更新' : '任务已创建', 'success');
            this.hideForm({ render: false });
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

taskCrud.init();

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
    isActive: () => $('tasks-panel')?.classList.contains('active') && $('taskFormPanel')?.style.display !== 'none',
    onTypeChange: type => {
        $('taskType').value = type === 'summary' ? 'summary' : 'send';
        $('taskTargetGroupsGroup').style.display = type === 'summary' ? 'none' : '';
        if (type === 'summary') loadSummaryGroupOptions();
    },
});

if (typeof registerGroupsLoadedHandler === 'function') {
    registerGroupsLoadedHandler(() => {
        if ($('taskFormPanel')?.style.display !== 'none') {
            updateTargetGroupsCheckboxes();
            refreshSummaryTargetOptions();
        }
    });
}
