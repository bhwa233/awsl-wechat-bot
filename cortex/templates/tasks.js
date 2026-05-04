const taskImageState = { message: '', filename: '' };
const taskFileState = { message: '', filename: '' };

function loadTaskServerInfo() {
    api('GET', '/api/health').then(r => {
        if (!r.ok || !r.data) return;
        const info = r.data;
        const el = $('timezoneInfo');
        if (!el) return;
        if (info.timezone && info.server_time) {
            const t = new Date(info.server_time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
            el.textContent = `服务器时区: ${info.timezone} (UTC${info.timezone_offset}) · 当前时间: ${t}`;
        }
    });
}

async function toggleTaskFormMode() {
    const taskType = $('taskType').value;
    $('taskMessageTypeGroup').style.display = taskType === 'send' ? '' : 'none';
    $('taskSummaryGroup').style.display = taskType === 'summary' ? '' : 'none';
    $('taskTargetGroupsGroup').style.display = taskType === 'send' ? '' : 'none';
    if (taskType === 'send') { toggleTaskMessageType(); return; }
    $('taskTextGroup').style.display = 'none';
    $('taskImageGroup').style.display = 'none';
    $('taskFileGroup').style.display = 'none';
    if (taskType === 'summary') await loadSummaryGroupOptions();
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
    return allGroups.map(g => {
        const sel = g.name === selectedValue ? ' selected' : '';
        return `<option value="${escapeHtml(g.name)}"${sel}>${escapeHtml(g.name)}</option>`;
    }).join('');
}

function addSummaryRow(groupId = '', groupName = '') {
    const container = $('summaryRows');
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

function getSummaryConfig() {
    const rows = document.querySelectorAll('.summary-row');
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

function populateSummaryRows(configStr) {
    const container = $('summaryRows');
    container.innerHTML = '';
    loadSummaryGroupOptions().then(() => {
        try {
            const cfg = JSON.parse(configStr);
            if (cfg.groups && Array.isArray(cfg.groups)) {
                for (const g of cfg.groups) addSummaryRow(g.group_id || '', g.group_name || '');
            }
        } catch { addSummaryRow(); }
    });
}

function toggleTaskMessageType() {
    const mt = $('taskMessageType').value;
    if ($('taskType').value !== 'send') {
        $('taskTextGroup').style.display = 'none';
        $('taskImageGroup').style.display = 'none';
        $('taskFileGroup').style.display = 'none';
        return;
    }
    $('taskTextGroup').style.display = mt === 'text' ? '' : 'none';
    $('taskImageGroup').style.display = mt === 'image' ? '' : 'none';
    $('taskFileGroup').style.display = mt === 'file' ? '' : 'none';
}

function previewTaskImage(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
        taskImageState.message = e.target.result.split(',')[1];
        $('taskImagePreviewImg').src = e.target.result;
        $('taskImagePreview').style.display = '';
    };
    reader.readAsDataURL(file);
}

function previewTaskFile(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
        taskFileState.message = e.target.result.split(',')[1];
        taskFileState.filename = file.name;
        $('taskFileName').textContent = file.name;
        $('taskFilePreview').style.display = '';
    };
    reader.readAsDataURL(file);
}

function resetTaskUpload() {
    taskImageState.message = ''; taskImageState.filename = '';
    taskFileState.message = ''; taskFileState.filename = '';
    const ip = $('taskImagePreview'); if (ip) ip.style.display = 'none';
    const ii = $('taskImagePreviewImg'); if (ii) ii.src = '';
    const fp = $('taskFilePreview'); if (fp) fp.style.display = 'none';
    const fn = $('taskFileName'); if (fn) fn.textContent = '';
}

function updateTargetGroupsCheckboxes() {
    const container = $('targetGroupsCheckboxes');
    if (!container) return;
    container.innerHTML = allGroups.map(g =>
        `<label class="checkbox-item"><input type="checkbox" value="${escapeHtml(g.name)}" name="targetGroups">${escapeHtml(g.name)}</label>`
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

const taskCrud = new CrudManager({
    apiPath: '/api/tasks', listId: 'tasksList', formPanelId: 'taskFormPanel', formId: 'taskForm',
    formTitleId: 'taskFormTitle', emptyText: '暂无定时任务',
    createTitle: '新建定时任务', editTitle: '编辑定时任务',
    createMsg: '任务已创建', updateMsg: '任务已更新', deleteMsg: '任务已删除',
    deleteConfirm: '确定要删除这个任务吗？',

    renderItem: task => {
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
        return `<div class="crud-item">${itemHeader(task.name, task.enabled)}<div class="info-grid">${infoItem('Cron', task.cron_expression)}${infoItem('类型', typeDisplay)}${infoItem('最后运行', task.last_run || '从未')}${contentItem}${targets}</div>${itemActions('taskCrud', task.id, task.enabled)}</div>`;
    },

    async fetchItem(id) {
        const r = await api('GET', `/api/tasks/${id}`);
        return r.ok ? r.data : null;
    },

    getFormData: () => {
        const taskType = $('taskType').value;
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
        data.message_type = $('taskMessageType').value;
        data.filename = '';
        if (data.message_type === 'text') data.message = $('taskMessage').value.trim();
        else if (data.message_type === 'image') data.message = taskImageState.message;
        else if (data.message_type === 'file') { data.message = taskFileState.message; data.filename = taskFileState.filename; }
        if (!data.message) { toast(data.message_type === 'text' ? '请输入消息' : '请选择内容', 'error'); return null; }
        if (data.message_type === 'file' && !data.filename) { toast('请选择文件', 'error'); return null; }
        return data;
    },

    populateForm: task => {
        $('taskName').value = task.name;
        $('cronExpression').value = task.cron_expression;
        $('taskEnabled').value = task.enabled.toString();
        resetTaskUpload();
        $('taskType').value = task.task_type || 'send';
        $('taskMessageType').value = task.message_type || 'text';
        toggleTaskFormMode();
        if (task.task_type === 'summary') { populateSummaryRows(task.message); $('taskMessage').value = ''; }
        else { $('summaryRows').innerHTML = ''; $('taskMessage').value = task.message_type === 'text' ? task.message : ''; }
        if (task.task_type === 'send' && task.message_type === 'image' && task.message) {
            taskImageState.message = task.message;
            $('taskImagePreviewImg').src = 'data:image/png;base64,' + task.message;
            $('taskImagePreview').style.display = '';
        }
        if (task.task_type === 'send' && task.message_type === 'file' && task.message) {
            taskFileState.message = task.message; taskFileState.filename = task.filename || '';
            $('taskFileName').textContent = task.filename || '';
            $('taskFilePreview').style.display = '';
        }
        document.querySelectorAll('input[name="targetGroups"]').forEach(cb => { cb.checked = task.target_groups?.includes(cb.value); });
    },

    resetForm: () => {
        resetTaskUpload();
        $('taskType').value = 'send'; $('taskMessageType').value = 'text'; $('summaryRows').innerHTML = '';
        toggleTaskFormMode();
        updateTargetGroupsCheckboxes();
    },
});
