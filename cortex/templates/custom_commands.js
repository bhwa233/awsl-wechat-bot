const customCommandImageState = { message: '', filename: '' };
const customCommandFileState = { message: '', filename: '' };

function toggleCustomCommandMessageType() {
    const mt = $('customCommandMessageType').value;
    $('customCommandTextGroup').style.display = mt === 'text' ? '' : 'none';
    $('customCommandImageGroup').style.display = mt === 'image' ? '' : 'none';
    $('customCommandFileGroup').style.display = mt === 'file' ? '' : 'none';
}

function previewCustomCommandImage(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
        customCommandImageState.message = e.target.result.split(',')[1];
        $('customCommandImagePreviewImg').src = e.target.result;
        $('customCommandImagePreview').style.display = '';
    };
    reader.readAsDataURL(file);
}

function previewCustomCommandFile(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
        customCommandFileState.message = e.target.result.split(',')[1];
        customCommandFileState.filename = file.name;
        $('customCommandFileName').textContent = file.name;
        $('customCommandFilePreview').style.display = '';
    };
    reader.readAsDataURL(file);
}

function resetCustomCommandUpload() {
    customCommandImageState.message = ''; customCommandImageState.filename = '';
    customCommandFileState.message = ''; customCommandFileState.filename = '';
    const ip = $('customCommandImagePreview'); if (ip) ip.style.display = 'none';
    const ii = $('customCommandImagePreviewImg'); if (ii) ii.src = '';
    const fp = $('customCommandFilePreview'); if (fp) fp.style.display = 'none';
    const fn = $('customCommandFileName'); if (fn) fn.textContent = '';
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
        if (cmd.message_type === 'image') {
            contentHtml = cmd.message ? `<img src="data:image/png;base64,${escapeHtml(cmd.message)}" style="max-width:160px;max-height:80px;border-radius:4px">` : '无图片';
            contentLabel = '图片';
            useRaw = true;
        } else if (cmd.message_type === 'file') {
            contentHtml = escapeHtml(cmd.filename || '未命名');
            contentLabel = '文件';
        } else {
            contentHtml = escapeHtml(cmd.message || '');
        }
        const contentItem = useRaw ? infoItemRaw(contentLabel, contentHtml) : infoItem(contentLabel, contentHtml);
        return `<div class="crud-item">${itemHeader(cmd.key, cmd.enabled)}<div class="info-grid">${infoItem('描述', cmd.description || '-')}${infoItem('类型', cmd.message_type)}${contentItem}</div>${itemActions('customCommandCrud', cmd.id, cmd.enabled)}</div>`;
    },

    async fetchItem(id) {
        const r = await api('GET', `/api/custom-commands/${id}`);
        return r.ok ? r.data : null;
    },

    getFormData: () => {
        const data = {
            key: $('customCommandKey').value.trim(),
            description: $('customCommandDescription').value.trim(),
            message_type: $('customCommandMessageType').value,
            filename: '',
            enabled: $('customCommandEnabled').value === 'true',
        };
        if (!data.key) { toast('请输入命令 Key', 'error'); return null; }
        if (/\s/.test(data.key)) { toast('命令 Key 不能包含空白字符', 'error'); return null; }
        if (data.message_type === 'text') data.message = $('customCommandMessage').value.trim();
        else if (data.message_type === 'image') data.message = customCommandImageState.message;
        else if (data.message_type === 'file') {
            data.message = customCommandFileState.message;
            data.filename = customCommandFileState.filename;
        }
        if (!data.message) { toast(data.message_type === 'text' ? '请输入回复内容' : '请选择内容', 'error'); return null; }
        if (data.message_type === 'file' && !data.filename) { toast('请选择文件', 'error'); return null; }
        return data;
    },

    populateForm: cmd => {
        $('customCommandKey').value = cmd.key;
        $('customCommandDescription').value = cmd.description || '';
        $('customCommandEnabled').value = cmd.enabled.toString();
        $('customCommandMessageType').value = cmd.message_type || 'text';
        $('customCommandMessage').value = cmd.message_type === 'text' ? cmd.message : '';
        resetCustomCommandUpload();
        if (cmd.message_type === 'image' && cmd.message) {
            customCommandImageState.message = cmd.message;
            $('customCommandImagePreviewImg').src = 'data:image/png;base64,' + cmd.message;
            $('customCommandImagePreview').style.display = '';
        }
        if (cmd.message_type === 'file' && cmd.message) {
            customCommandFileState.message = cmd.message;
            customCommandFileState.filename = cmd.filename || '';
            $('customCommandFileName').textContent = cmd.filename || '';
            $('customCommandFilePreview').style.display = '';
        }
        toggleCustomCommandMessageType();
    },

    resetForm: () => {
        resetCustomCommandUpload();
        $('customCommandMessageType').value = 'text';
        $('customCommandEnabled').value = 'true';
        toggleCustomCommandMessageType();
    },
});
