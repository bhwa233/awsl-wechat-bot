import { h, useEffect, useLayoutEffect, useRef, useState, useApp, Button, Select } from './ui.js';
import { formatFileSize } from './api.js';

export const emptyMessage = () => ({ message_type: 'text', message: '', filename: '' });
export const messageTypes = [
    ['text', '文本'],
    ['voice', '语音'],
    ['voice_file', '语音文件'],
    ['image', '图片'],
    ['file', '文件'],
    ['summary', '总结'],
];

export function messagePayload(value, capabilities) {
    if (value.reading) throw new Error('附件仍在读取，请稍候');
    const type = value.message_type;
    if (type === 'summary') return { message_type: type, message: '', filename: '' };
    if (['voice', 'voice_file'].includes(type) && !capabilities.voice) throw new Error('当前平台不支持语音发送');
    const message = ['text', 'voice'].includes(type) ? value.message.trim() : value.message;
    if (!message) throw new Error(['text', 'voice'].includes(type) ? '请输入消息内容' : '请选择附件');
    if (['file', 'voice_file'].includes(type) && !value.filename) throw new Error('附件缺少文件名，请重新选择');
    const limit = capabilities.max_binary_file_bytes || 0;
    const size = Math.floor((message.length * 3) / 4) - (message.endsWith('==') ? 2 : message.endsWith('=') ? 1 : 0);
    if (limit && !['text', 'voice'].includes(type) && size > limit)
        throw new Error(`文件不能超过 ${formatFileSize(limit)}`);
    if (value.reply_to_message && (type !== 'text' || !capabilities.reply)) throw new Error('当前发送方式不支持引用');
    return {
        message_type: type, message, filename: ['file', 'voice_file'].includes(type) ? value.filename : '',
        ...(value.reply_to_message ? { reply_to_message: value.reply_to_message } : {}),
    };
}

export function MessageComposer({
    value,
    onChange,
    summary = false,
    onSend,
    busy = false,
    disabled = false,
    sendDisabled = false,
    quietSendDisabled = false,
    active = true,
    children,
    id = 'composer',
}) {
    const { capabilities, toast } = useApp();
    const [dragging, setDragging] = useState(false);
    const [reading, setReading] = useState(false);
    const [dimensions, setDimensions] = useState('');
    const reader = useRef(null);
    const root = useRef(null);
    const generation = useRef(0);
    const type = value.message_type;
    const attachment = ['image', 'file', 'voice_file'].includes(type);
    const size =
        Math.floor((value.message.length * 3) / 4) -
        (value.message.endsWith('==') ? 2 : value.message.endsWith('=') ? 1 : 0);
    const preview = type === 'image' && value.message ? `data:image/png;base64,${value.message}` : '';
    const choices = messageTypes.filter(
        ([key]) => (summary || key !== 'summary') && (capabilities.voice || !['voice', 'voice_file'].includes(key))
    );
    if (!choices.some(([key]) => key === type))
        choices.push([type, `${messageTypes.find(([key]) => key === type)?.[1] || type}（不可用）`]);

    useEffect(
        () => () => {
            generation.current++;
            reader.current?.abort();
        },
        []
    );
    useLayoutEffect(() => {
        generation.current++;
        reader.current?.abort();
        setReading(false);
        setDimensions('');
    }, [type]);
    useEffect(() => {
        let live = true;
        setDimensions('');
        if (preview) {
            const image = new Image();
            image.onload = () => {
                if (live) setDimensions(`${image.naturalWidth}×${image.naturalHeight}`);
            };
            image.src = preview;
        }
        return () => {
            live = false;
        };
    }, [preview]);

    function loadFile(file) {
        if (!file || busy || disabled || !attachment) return;
        const max = capabilities.max_binary_file_bytes;
        if (max && file.size > max) {
            toast(`文件不能超过 ${formatFileSize(max)}`, 'error');
            return;
        }
        if (type === 'image' && !file.type.startsWith('image/')) {
            toast('请选择图片文件', 'error');
            return;
        }
        if (type === 'voice_file' && !file.name.toLowerCase().endsWith('.mp3')) {
            toast('语音文件仅支持 MP3', 'error');
            return;
        }
        reader.current?.abort();
        const token = ++generation.current;
        const next = new FileReader();
        reader.current = next;
        setReading(true);
        onChange({ ...value, reading: true });
        next.onload = () => {
            if (generation.current !== token) return;
            onChange({
                ...value,
                reading: false,
                message: String(next.result).split(',')[1] || '',
                filename: file.name,
            });
            setReading(false);
        };
        next.onerror = () => {
            if (generation.current === token) {
                setReading(false);
                onChange({ ...value, reading: false });
                toast('读取附件失败，请重试', 'error');
            }
        };
        next.readAsDataURL(file);
    }
    useEffect(() => {
        if (!active || !attachment || disabled || busy) return;
        const paste = (event) => {
            if (!root.current?.getClientRects().length) return;
            const modal = [...document.querySelectorAll('.modal-overlay')].find((el) => el.getClientRects().length);
            if (modal && !root.current.closest('.modal-overlay')) return;
            const file = [...(event.clipboardData?.files || [])].find(
                (f) => type !== 'image' || f.type.startsWith('image/')
            );
            if (!file) return;
            event.preventDefault();
            loadFile(file);
        };
        document.addEventListener('paste', paste);
        return () => document.removeEventListener('paste', paste);
    }, [active, type, busy, disabled, value, capabilities]);

    return h(
        'div',
        { class: `message-composer ${onSend ? 'chat-compose' : ''} ${busy ? 'sending' : ''}`, ref: root, id },
        value.reply_to_message && h(
            'div', { class: 'composer-reply', role: 'status', 'aria-label': '引用预览' },
            h('div', null, h('strong', null, '引用'), h('span', null, value.reply_to_message)),
            h(Button, {
                class: 'btn-xs', 'aria-label': '取消引用', disabled: busy || disabled,
                onClick: () => onChange({ ...value, reply_to_message: undefined }),
            }, '×')
        ),
        h(
            'div',
            { class: 'compose-row' },
            children,
            h(Select, {
                class: 'composer-type-select',
                'aria-label': '消息类型',
                value: type,
                options: choices,
                disabled: busy || reading || disabled,
                onValue: (message_type) => onChange({ message_type, message: '', filename: '' }),
            }),
            h(
                'div',
                { class: 'composer-type-control', role: 'group', 'aria-label': '消息类型' },
                choices.map(([key, label]) =>
                    h(
                        'button',
                        {
                            type: 'button',
                            key,
                            disabled: busy || reading || disabled,
                            class: `composer-type-option ${type === key ? 'active' : ''}`,
                            'aria-pressed': type === key,
                            'data-value': key,
                            onClick: () => {
                                if (type !== key) onChange({ message_type: key, message: '', filename: '' });
                            },
                        },
                        label
                    )
                )
            )
        ),
        h(
            'div',
            { class: 'compose-row compose-row-main' },
            ['text', 'voice'].includes(type) &&
                h(
                    'div',
                    { class: 'chat-input-body' },
                    h('textarea', {
                        id: `${id}-text`,
                        'aria-label': '消息内容',
                        value: value.message,
                        rows: onSend ? 2 : 4,
                        disabled: busy || disabled,
                        placeholder: type === 'voice' ? '输入要合成语音的文字…' : '输入消息…',
                        onInput: (e) => onChange({ ...value, message: e.currentTarget.value }),
                        onKeyDown: (e) => {
                            if (onSend && e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
                                e.preventDefault();
                                if (!busy && !disabled && !sendDisabled && value.message.trim()) onSend();
                            }
                        },
                    })
                ),
            attachment &&
                h(
                    'label',
                    {
                        class: `file-dropzone ${dragging ? 'dragover' : ''} ${value.message ? 'has-file' : ''}`,
                        onDragOver: (e) => {
                            e.preventDefault();
                            setDragging(true);
                        },
                        onDragLeave: () => setDragging(false),
                        onDrop: (e) => {
                            e.preventDefault();
                            setDragging(false);
                            loadFile(e.dataTransfer.files[0]);
                        },
                    },
                    h('input', {
                        type: 'file',
                        hidden: true,
                        disabled: busy || disabled,
                        id: `${id}-file`,
                        accept: type === 'image' ? 'image/*' : type === 'voice_file' ? 'audio/mpeg,.mp3' : '',
                        onChange: (e) => {
                            loadFile(e.currentTarget.files[0]);
                            e.currentTarget.value = '';
                        },
                    }),
                    preview && h('img', { src: preview, alt: '附件预览' }),
                    h(
                        'span',
                        null,
                        reading
                            ? '正在读取附件…'
                            : value.message
                              ? [value.filename || '图片', formatFileSize(size), dimensions].filter(Boolean).join(' · ')
                              : type === 'voice_file'
                                ? '选择 MP3 文件，最长 60 秒'
                                : '点击选择 / 拖拽 / 粘贴附件'
                    )
                ),
            onSend &&
                h(
                    Button,
                    {
                        variant: 'green',
                        busy: busy || reading,
                        disabled: disabled || sendDisabled || (type !== 'summary' && !value.message.trim()),
                        quietDisabled: quietSendDisabled && !disabled && (type === 'summary' || !!value.message.trim()),
                        onClick: onSend,
                        id: `${id}-send`,
                    },
                    type === 'summary' ? '生成总结' : '发送'
                )
        ),
        onSend && h('div', { class: 'compose-hint' }, 'Enter 发送 · Shift + Enter 换行')
    );
}

export function MessagePreview({ type, message, filename }) {
    if (type === 'image' && message)
        return h('img', {
            class: 'res-preview-img',
            src: `data:image/png;base64,${message}`,
            loading: 'lazy',
            alt: '图片预览',
        });
    const text =
        type === 'summary'
            ? '📊 群聊总结'
            : ['file', 'voice_file'].includes(type)
              ? `📎 ${filename || '附件'}`
              : message;
    return h('span', { class: 'preview-text' }, text || '—');
}
