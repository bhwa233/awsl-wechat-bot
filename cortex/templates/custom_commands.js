let customCommandMessageComposer = null;

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

const customCommandCrud = new CrudManager({
    apiPath: '/api/custom-commands', listId: 'customCommandsList',
    formPanelId: 'customCommandFormPanel', formId: 'customCommandForm',
    formTitleId: 'customCommandFormTitle', emptyText: '暂无自定义命令',
    createTitle: '新建自定义命令', editTitle: '编辑自定义命令',
    createMsg: '自定义命令已创建', updateMsg: '自定义命令已更新', deleteMsg: '自定义命令已删除',
    deleteConfirm: '确定要删除这个自定义命令吗？',

    renderItem: cmd => {
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
        return `<div class="crud-item">${itemHeader(cmd.key, cmd.enabled)}<div class="info-grid">${infoItem('描述', cmd.description || '-')}${infoItem('类型', actionType)}${contentItem}${targets}</div>${itemActions('customCommandCrud', cmd.id, cmd.enabled)}</div>`;
    },

    async fetchItem(id) {
        const r = await api('GET', `/api/custom-commands/${id}`);
        return r.ok ? r.data : null;
    },

    getFormData: () => {
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

    populateForm: cmd => {
        $('customCommandKey').value = cmd.key;
        $('customCommandDescription').value = cmd.description || '';
        $('customCommandEnabled').value = cmd.enabled.toString();
        const actionType = cmd.action_type || cmd.message_type || 'text';
        $('customCommandMessageType').value = actionType;
        $('customCommandMessage').value = actionType === 'text' ? cmd.message : '';
        resetCustomCommandUpload();
        updateCustomCommandTargetGroupsCheckboxes();
        if (actionType === 'image' && cmd.message) {
            customCommandMessageComposer.setAttachment(cmd.message, '', 'data:image/png;base64,' + cmd.message);
        }
        if (actionType === 'file' && cmd.message) {
            customCommandMessageComposer.setAttachment(cmd.message, cmd.filename || '');
        }
        document.querySelectorAll('input[name="customCommandTargetGroups"]').forEach(cb => {
            cb.checked = cmd.target_groups?.includes(cb.value);
        });
        toggleCustomCommandMessageType({ resetUpload: false });
    },

    resetForm: () => {
        resetCustomCommandUpload();
        $('customCommandMessageType').value = 'text';
        $('customCommandEnabled').value = 'true';
        updateCustomCommandTargetGroupsCheckboxes();
        toggleCustomCommandMessageType();
    },
});

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
        if ($('customCommandFormPanel')?.style.display !== 'none') updateCustomCommandTargetGroupsCheckboxes();
    });
}
