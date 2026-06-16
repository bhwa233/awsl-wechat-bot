const messageComposerRegistry = [];

function formatComposerFileSize(bytes) {
    if (!bytes || bytes < 0) return '';
    const units = ['B', 'KB', 'MB', 'GB'];
    let idx = 0;
    let value = bytes;
    while (value >= 1024 && idx < units.length - 1) {
        value /= 1024;
        idx++;
    }
    return (idx === 0 ? value : value.toFixed(1)) + ' ' + units[idx];
}

function getComposerBase64Size(value) {
    if (!value) return 0;
    const base64 = String(value).split(',').pop().replace(/\s/g, '');
    if (!base64) return 0;
    const padding = base64.endsWith('==') ? 2 : base64.endsWith('=') ? 1 : 0;
    return Math.max(0, Math.floor(base64.length * 3 / 4) - padding);
}

function formatComposerAttachmentInfo(label, bytes, dimensions = '') {
    const parts = [label || '已选择文件'];
    const size = formatComposerFileSize(bytes);
    if (size) parts.push(size);
    if (dimensions) parts.push(dimensions);
    return parts.join(' · ');
}

class MessageComposer {
    constructor(cfg) {
        this.cfg = cfg;
        this.state = { message: '', filename: '' };
        this.infoToken = 0;
        this.typeEl = $(cfg.typeId);
        this.typeControlEl = null;
        this.textEl = cfg.textInputId ? $(cfg.textInputId) : null;
        this.fileEl = cfg.fileInputId ? $(cfg.fileInputId) : null;
        this.dropzoneEl = cfg.dropzoneId ? $(cfg.dropzoneId) : null;
        this.previewEl = cfg.previewId ? $(cfg.previewId) : null;
        this.infoEl = cfg.infoId ? $(cfg.infoId) : null;
        this._enhanceTypeControl();
        this._bind();
        this.syncType({ resetUpload: false });
        messageComposerRegistry.push(this);
    }

    getType() {
        return this.typeEl?.value || 'text';
    }

    isVisible() {
        if (this.cfg.isActive && !this.cfg.isActive()) return false;
        const root = this.typeEl?.closest('.panel, .form-panel, .chat-input') || this.typeEl;
        if (!root) return false;
        return root.offsetParent !== null;
    }

    syncType(opts = {}) {
        const type = this.getType();
        const isAttachment = type === 'image' || type === 'file';
        this._setDisplay(this.cfg.textGroupId, type === 'text');
        this._setDisplay(this.cfg.attachmentGroupId, isAttachment);
        this._setDisplay(this.cfg.summaryGroupId, type === 'summary');
        if (this.fileEl) {
            this.fileEl.accept = type === 'image' ? 'image/*' : '';
        }
        if (this.dropzoneEl) {
            this.dropzoneEl.dataset.type = type;
        }
        if (opts.resetUpload !== false && isAttachment) {
            this.resetUpload();
        }
        this._syncTypeControl();
        if (this.cfg.onTypeChange) this.cfg.onTypeChange(type);
    }

    resetUpload() {
        this.infoToken++;
        this.state = { message: '', filename: '' };
        if (this.fileEl) this.fileEl.value = '';
        if (this.dropzoneEl) this.dropzoneEl.classList.remove('has-file', 'dragover');
        if (this.previewEl) {
            this.previewEl.hidden = true;
            this.previewEl.removeAttribute('src');
        }
        if (this.infoEl) this.infoEl.textContent = this.cfg.emptyLabel || '点击选择 / 拖拽 / Ctrl+V 粘贴';
    }

    reset() {
        this.resetUpload();
        if (this.textEl) this.textEl.value = '';
        if (this.typeEl) this.typeEl.value = this.cfg.defaultType || 'text';
        this.syncType({ resetUpload: false });
    }

    setAttachment(message, filename = '', previewSrc = '') {
        const token = ++this.infoToken;
        this.state = { message: message || '', filename: filename || '' };
        if (this.dropzoneEl) this.dropzoneEl.classList.toggle('has-file', !!message);
        if (this.previewEl) {
            if (previewSrc) {
                this.previewEl.src = previewSrc;
                this.previewEl.hidden = false;
            } else {
                this.previewEl.hidden = true;
                this.previewEl.removeAttribute('src');
            }
        }
        if (this.infoEl) {
            const bytes = getComposerBase64Size(message);
            const label = filename || (previewSrc ? '图片' : '已选择文件');
            this.infoEl.textContent = formatComposerAttachmentInfo(label, bytes);
            if (previewSrc) this._updateImageInfo(previewSrc, label, bytes, token);
        }
    }

    loadFile(file) {
        if (!file) return;
        const type = this.getType();
        if (type === 'image' && !file.type.startsWith('image/')) {
            toast('请选择图片文件', 'error');
            return;
        }
        const reader = new FileReader();
        reader.onload = e => {
            const dataUrl = e.target.result;
            this.state.message = String(dataUrl).split(',')[1] || '';
            this.state.filename = file.name;
            if (this.dropzoneEl) this.dropzoneEl.classList.add('has-file');
            if (this.previewEl) {
                if (file.type.startsWith('image/')) {
                    this.previewEl.src = dataUrl;
                    this.previewEl.hidden = false;
                } else {
                    this.previewEl.hidden = true;
                    this.previewEl.removeAttribute('src');
                }
            }
            if (this.infoEl) {
                this.infoEl.textContent = formatComposerAttachmentInfo(file.name, file.size);
                if (file.type.startsWith('image/')) this._updateImageInfo(dataUrl, file.name, file.size, ++this.infoToken);
            }
        };
        reader.readAsDataURL(file);
    }

    getText() {
        return this.textEl ? this.textEl.value.trim() : '';
    }

    getPayload(messages = {}) {
        const type = this.getType();
        if (type === 'summary') {
            return { message_type: 'summary', message: '', filename: '' };
        }
        if (type === 'text') {
            const message = this.getText();
            if (!message) {
                if (messages.text) toast(messages.text, 'error');
                return null;
            }
            return { message_type: 'text', message, filename: '' };
        }
        if (!this.state.message) {
            toast(messages.attachment || '请选择内容', 'error');
            return null;
        }
        return {
            message_type: type,
            message: this.state.message,
            filename: type === 'file' ? this.state.filename : '',
        };
    }

    _bind() {
        if (this.typeEl) {
            this.typeEl.addEventListener('change', () => this.syncType());
        }
        if (this.fileEl) {
            this.fileEl.addEventListener('change', event => this.loadFile(event.target.files[0]));
        }
        if (!this.dropzoneEl) return;
        this.dropzoneEl.addEventListener('dragover', event => {
            event.preventDefault();
            this.dropzoneEl.classList.add('dragover');
        });
        this.dropzoneEl.addEventListener('dragleave', () => this.dropzoneEl.classList.remove('dragover'));
        this.dropzoneEl.addEventListener('drop', event => {
            event.preventDefault();
            this.dropzoneEl.classList.remove('dragover');
            this.loadFile(event.dataTransfer.files[0]);
        });
    }

    _setDisplay(id, show) {
        if (!id) return;
        const el = $(id);
        if (el) el.style.display = show ? '' : 'none';
    }

    _enhanceTypeControl() {
        if (!this.typeEl || this.typeEl.dataset.enhanced === 'true') return;
        const options = Array.from(this.typeEl.options).filter(option => option.value);
        if (!options.length) return;

        const iconMap = { text: 'Aa', image: 'Img', file: 'File', summary: 'Sum' };
        const control = document.createElement('div');
        control.className = 'composer-type-control';
        control.setAttribute('role', 'group');
        control.setAttribute('aria-label', '消息类型');

        for (const option of options) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'composer-type-option';
            button.dataset.value = option.value;
            button.innerHTML = `<span class="composer-type-icon">${iconMap[option.value] || option.textContent.slice(0, 2)}</span><span>${option.textContent}</span>`;
            button.addEventListener('click', () => {
                if (this.typeEl.value === option.value) return;
                this.typeEl.value = option.value;
                this.typeEl.dispatchEvent(new Event('change', { bubbles: true }));
            });
            control.appendChild(button);
        }

        this.typeEl.classList.add('composer-type-source');
        this.typeEl.dataset.enhanced = 'true';
        this.typeEl.insertAdjacentElement('afterend', control);
        this.typeControlEl = control;
    }

    _syncTypeControl() {
        if (!this.typeControlEl) return;
        const type = this.getType();
        this.typeControlEl.querySelectorAll('.composer-type-option').forEach(button => {
            const active = button.dataset.value === type;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
    }

    _updateImageInfo(src, label, bytes, token) {
        if (!this.infoEl) return;
        const img = new Image();
        img.onload = () => {
            if (this.infoToken !== token) return;
            const dimensions = img.naturalWidth && img.naturalHeight ? `${img.naturalWidth}×${img.naturalHeight}` : '';
            this.infoEl.textContent = formatComposerAttachmentInfo(label, bytes, dimensions);
        };
        img.src = src;
    }
}

document.addEventListener('paste', event => {
    const composer = messageComposerRegistry.find(item => {
        const type = item.getType();
        return item.isVisible() && (type === 'image' || type === 'file');
    });
    if (!composer) return;
    const items = event.clipboardData?.items;
    if (!items) return;
    const type = composer.getType();
    for (const item of items) {
        if (item.kind !== 'file') continue;
        if (type === 'image' && !item.type.startsWith('image/')) continue;
        const file = item.getAsFile();
        if (!file) continue;
        event.preventDefault();
        if (type === 'image') {
            const ext = item.type.split('/')[1] || 'png';
            composer.loadFile(new File([file], `pasted-${Date.now()}.${ext}`, { type: item.type }));
            toast('已粘贴图片', 'success');
            return;
        }
        composer.loadFile(file);
        toast('已粘贴文件', 'success');
        return;
    }
});
